"""Shared HTTP client (retries, backoff, rate limiting) and pagination loop."""
from __future__ import annotations

import logging
import random
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

from config import Settings

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
MAX_CONSECUTIVE_PAGE_FAILURES = 2


class FetchError(Exception):
    """Raised when a URL could not be fetched after all retries."""

    def __init__(self, url: str, message: str, status_code: Optional[int] = None):
        super().__init__(f"{url}: {message}")
        self.url = url
        self.status_code = status_code


@dataclass
class ScrapeResult:
    source: str
    records: list = field(default_factory=list)
    pages_fetched: int = 0
    pages_failed: int = 0
    items_failed_parsing: int = 0
    detail_requests: int = 0
    detail_requests_failed: int = 0
    errors: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add_error(self, message: str) -> None:
        log.error(message)
        with self._lock:
            self.errors.append(message)

    def incr(self, counter: str, amount: int = 1) -> None:
        """Thread-safe counter increment (detail pages are fetched concurrently)."""
        with self._lock:
            setattr(self, counter, getattr(self, counter) + amount)


class HttpClient:
    """requests.Session wrapper: timeout, retry with exponential backoff, global rate limit."""

    def __init__(self, settings: Settings, session: Optional[requests.Session] = None):
        self.settings = settings
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent})
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._robots = {}

    # -- rate limiting -----------------------------------------------------
    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_request + self.settings.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    # -- robots.txt (fail-open if robots.txt itself is unavailable) ---------
    def _allowed(self, url: str) -> bool:
        if not self.settings.respect_robots:
            return True
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                self._throttle()
                resp = self.session.get(origin + "/robots.txt", timeout=self.settings.timeout)
                if resp.status_code == 200:
                    parser.parse(resp.text.splitlines())
                else:  # no robots.txt (e.g. 404) -> nothing is disallowed
                    parser.parse([])
            except requests.RequestException as exc:
                log.warning("Could not read robots.txt for %s (%s); assuming allowed", origin, exc)
                parser.parse([])
            self._robots[origin] = parser
        return self._robots[origin].can_fetch(self.settings.user_agent, url)

    # -- main entry point ----------------------------------------------------
    def get_html(self, url: str) -> str:
        if not self._allowed(url):
            raise FetchError(url, "disallowed by robots.txt")
        attempts = self.settings.max_retries + 1
        last_problem = "unknown error"
        last_status = None
        for attempt in range(1, attempts + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=self.settings.timeout)
                last_status = resp.status_code
                if resp.status_code == 200:
                    resp.encoding = "utf-8"  # sites omit charset; avoids 'Â£' mojibake
                    return resp.text
                last_problem = f"HTTP {resp.status_code}"
                if resp.status_code not in RETRYABLE_STATUS:
                    raise FetchError(url, last_problem, resp.status_code)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_problem = f"{type(exc).__name__}: {exc}"
            except requests.RequestException as exc:
                raise FetchError(url, f"{type(exc).__name__}: {exc}") from exc

            if attempt < attempts:
                sleep_for = self.settings.backoff_factor * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
                log.warning("Attempt %d/%d failed for %s (%s); retrying in %.1fs",
                            attempt, attempts, url, last_problem, sleep_for)
                time.sleep(sleep_for)
        raise FetchError(url, f"gave up after {attempts} attempts ({last_problem})", last_status)


def paginate(
    client: HttpClient,
    start_url: str,
    parse_page: Callable[[str, str], tuple],
    guess_next: Callable[[str], Optional[str]],
    max_pages: Optional[int],
    result: ScrapeResult,
) -> None:
    """Follow 'next' links until there are none.

    parse_page(html, url) -> (items, next_url_or_None).
    If a page fails (network error or unparsable), we log it and try the
    conventional next-page URL (guess_next) so one bad page doesn't lose the rest.
    A 404 on a guessed URL is treated as "end of the listing", not as a failure.
    """
    url: Optional[str] = start_url
    seen = set()
    attempted = 0
    consecutive_failures = 0
    guessed = False
    while url and url not in seen and (max_pages is None or attempted < max_pages):
        seen.add(url)
        attempted += 1
        next_url = None
        try:
            html = client.get_html(url)
            items, next_url = parse_page(html, url)
        except FetchError as exc:
            if guessed and exc.status_code == 404:
                log.info("Reached end of listing (404 at guessed URL %s)", url)
                return
            result.pages_failed += 1
            result.add_error(f"Page failed: {exc}")
            consecutive_failures += 1
            items = []
        except Exception as exc:  # unexpected markup etc.
            result.pages_failed += 1
            result.add_error(f"Page could not be parsed: {url}: {type(exc).__name__}: {exc}")
            consecutive_failures += 1
            items = []
        else:
            consecutive_failures = 0
            result.pages_fetched += 1
            result.records.extend(items)
            log.info("Page %d OK: %s (%d items, running total %d)",
                     attempted, url, len(items), len(result.records))

        if consecutive_failures:
            if consecutive_failures >= MAX_CONSECUTIVE_PAGE_FAILURES:
                result.add_error(f"Stopping pagination after {consecutive_failures} consecutive failures")
                return
            url, guessed = guess_next(url), True
        else:
            url, guessed = next_url, False

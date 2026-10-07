"""Scraper for https://quotes.toscrape.com/ (all selectors for this site live here)."""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import Settings
from scrapers.base import FetchError, HttpClient, ScrapeResult, paginate

log = logging.getLogger(__name__)

SOURCE_NAME = "Quotes to Scrape"
BASE_URL = "https://quotes.toscrape.com/"
_PAGE_RE = re.compile(r"/page/(\d+)/?$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(node) -> Optional[str]:
    return node.get_text(" ", strip=True) if node is not None else None


def guess_next_page(url: str) -> Optional[str]:
    """Conventional next-page URL, only used when a page failed and no 'next' link is known."""
    m = _PAGE_RE.search(url)
    if m:
        return _PAGE_RE.sub(f"/page/{int(m.group(1)) + 1}/", url)
    return urljoin(BASE_URL, "page/2/")


def parse_listing_page(html: str, page_url: str, on_item_error=None) -> tuple[list[dict], Optional[str]]:
    """Return (raw quote records, absolute next-page URL or None) for one listing page."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for block in soup.select("div.quote"):
        try:
            author_link = block.select_one("a[href^='/author/']")
            items.append({
                "source": SOURCE_NAME,
                "record_type": "quote",
                # Quotes have no permalink on this site, so the listing page they were found on is the URL.
                "source_url": page_url,
                "name_or_title": _text(block.select_one("span.text")),
                "author": _text(block.select_one("small.author")),
                "author_url": urljoin(page_url, author_link["href"]) if author_link else None,
                "tags": [_text(a) for a in block.select("div.tags a.tag")],
                "scraped_at": _now(),
            })
        except Exception as exc:
            log.warning("Skipping malformed quote block on %s: %s", page_url, exc)
            if on_item_error:
                on_item_error()
    next_link = soup.select_one("li.next a[href]")
    next_url = urljoin(page_url, next_link["href"]) if next_link else None
    return items, next_url


def parse_author_page(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    return {
        "author_born_date": _text(soup.select_one("span.author-born-date")),
        "author_born_location": _text(soup.select_one("span.author-born-location")),
    }


class QuotesScraper:
    def __init__(self, client: HttpClient, settings: Settings):
        self.client = client
        self.settings = settings

    def scrape(self) -> ScrapeResult:
        result = ScrapeResult(source=SOURCE_NAME)
        log.info("Starting %s", SOURCE_NAME)

        def parse(html: str, url: str):
            return parse_listing_page(html, url, on_item_error=lambda: result.incr("items_failed_parsing"))

        paginate(self.client, BASE_URL, parse, guess_next_page, self.settings.max_pages, result)

        if self.settings.fetch_authors and result.records:
            self._add_author_info(result)
        log.info("Finished %s: %d raw records", SOURCE_NAME, len(result.records))
        return result

    def _add_author_info(self, result: ScrapeResult) -> None:
        """Fetch each distinct author page once and copy the info onto that author's quotes."""
        urls = sorted({r["author_url"] for r in result.records if r.get("author_url")})

        def fetch(url: str):
            result.incr("detail_requests")
            try:
                return url, parse_author_page(self.client.get_html(url))
            except FetchError as exc:
                result.incr("detail_requests_failed")
                result.add_error(f"Author page failed (quotes kept without author info): {exc}")
            except Exception as exc:
                result.incr("detail_requests_failed")
                result.add_error(f"Author page unparsable: {url}: {type(exc).__name__}: {exc}")
            return url, {}

        log.info("Fetching %d author pages with %d workers", len(urls), self.settings.workers)
        with ThreadPoolExecutor(max_workers=self.settings.workers) as pool:
            info = dict(pool.map(fetch, urls))
        for record in result.records:
            record.update(info.get(record.get("author_url"), {}))

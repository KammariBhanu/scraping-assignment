"""Scraper for https://books.toscrape.com/ (all selectors for this site live here)."""
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

SOURCE_NAME = "Books to Scrape"
BASE_URL = "https://books.toscrape.com/"
_PAGE_RE = re.compile(r"page-(\d+)\.html$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(node) -> Optional[str]:
    return node.get_text(" ", strip=True) if node is not None else None


def guess_next_page(url: str) -> Optional[str]:
    """Conventional next-page URL, only used when a page failed and no 'next' link is known."""
    m = _PAGE_RE.search(url)
    if m:
        return _PAGE_RE.sub(f"page-{int(m.group(1)) + 1}.html", url)
    return urljoin(BASE_URL, "catalogue/page-2.html")


def parse_listing_page(html: str, page_url: str, on_item_error=None) -> tuple[list[dict], Optional[str]]:
    """Return (raw book records, absolute next-page URL or None) for one listing page."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.select("article.product_pod"):
        try:
            link = card.select_one("h3 a")
            href = link.get("href") if link else None
            title = (link.get("title") or _text(link)) if link else None
            rating_node = card.select_one("p.star-rating")
            rating_word = None
            if rating_node is not None:
                classes = [c for c in rating_node.get("class", []) if c != "star-rating"]
                rating_word = classes[0] if classes else None
            items.append({
                "source": SOURCE_NAME,
                "record_type": "book",
                "source_url": urljoin(page_url, href) if href else None,
                "name_or_title": title,
                "price": _text(card.select_one("p.price_color")),
                "rating": rating_word,
                "availability": _text(card.select_one("p.availability")),
                "category": None,      # filled from the product page
                "description": None,   # filled from the product page
                "scraped_at": _now(),
            })
        except Exception as exc:  # one malformed card must not kill the page
            log.warning("Skipping malformed book card on %s: %s", page_url, exc)
            if on_item_error:
                on_item_error()
    next_link = soup.select_one("li.next a[href]")
    next_url = urljoin(page_url, next_link["href"]) if next_link else None
    return items, next_url


def parse_detail_page(html: str) -> dict:
    """Extract category + description from a product page. Missing pieces become None."""
    soup = BeautifulSoup(html, "html.parser")
    crumbs = [_text(li) for li in soup.select("ul.breadcrumb li")]
    # Breadcrumb: Home > Books > <Category> > <Title>
    category = crumbs[2] if len(crumbs) >= 4 else None
    desc_header = soup.select_one("#product_description")
    description = _text(desc_header.find_next_sibling("p")) if desc_header else None
    return {"category": category, "description": description}


class BooksScraper:
    def __init__(self, client: HttpClient, settings: Settings):
        self.client = client
        self.settings = settings

    def scrape(self) -> ScrapeResult:
        result = ScrapeResult(source=SOURCE_NAME)
        log.info("Starting %s", SOURCE_NAME)

        def parse(html: str, url: str):
            def bump():
                result.incr("items_failed_parsing")
            return parse_listing_page(html, url, on_item_error=bump)

        paginate(self.client, BASE_URL, parse, guess_next_page, self.settings.max_pages, result)

        if self.settings.fetch_details and result.records:
            self._add_details(result)
        log.info("Finished %s: %d raw records", SOURCE_NAME, len(result.records))
        return result

    def _add_details(self, result: ScrapeResult) -> None:
        def fetch(record: dict) -> None:
            url = record.get("source_url")
            if not url:
                return
            result.incr("detail_requests")
            try:
                detail = parse_detail_page(self.client.get_html(url))
                record.update({k: v for k, v in detail.items() if v is not None})
            except FetchError as exc:
                result.incr("detail_requests_failed")
                result.add_error(f"Detail page failed (record kept without category/description): {exc}")
            except Exception as exc:
                result.incr("detail_requests_failed")
                result.add_error(f"Detail page unparsable: {url}: {type(exc).__name__}: {exc}")

        log.info("Fetching %d product pages with %d workers", len(result.records), self.settings.workers)
        with ThreadPoolExecutor(max_workers=self.settings.workers) as pool:
            list(pool.map(fetch, result.records))

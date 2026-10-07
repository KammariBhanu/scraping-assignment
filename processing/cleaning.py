"""Reusable cleaning helpers + clean_record(): raw scraped dict -> standardized record.

Nothing here touches the network or HTML, so it is easy to unit test.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from config import SCHEMA_COLUMNS

MISSING_TOKENS = {"", "n/a", "na", "none", "null", "nan", "-", "--", "unknown"}
RATING_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
CURRENCY_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}
_QUOTE_CHARS = "\"'\u201c\u201d\u2018\u2019\u00ab\u00bb"


def normalize_missing(value: Any) -> Any:
    """Turn empty / placeholder values ('N/A', 'null', '') into None."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in MISSING_TOKENS:
        return None
    return value


def clean_text(value: Any) -> Optional[str]:
    """Unicode-normalize (NFKC), collapse all whitespace (incl. nbsp), trim; missing -> None."""
    value = normalize_missing(value)
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return normalize_missing(text)


def strip_wrapping_quotes(text: Optional[str]) -> Optional[str]:
    """Remove the decorative quote marks the site wraps around quote text."""
    if text is None:
        return None
    return clean_text(text.strip(_QUOTE_CHARS + " "))


def parse_price(value: Any) -> tuple[Optional[float], Optional[str]]:
    """'£51.77' -> (51.77, 'GBP'). Also copes with mojibake 'Â£51.77' and '1,234.50'.

    Returns (None, currency) when no number can be found; the validator then decides.
    """
    text = clean_text(value)
    if text is None:
        return None, None
    currency = next((code for sym, code in CURRENCY_SYMBOLS.items() if sym in text), None)
    match = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return None, currency
    try:
        return round(float(match.group().replace(",", "")), 2), currency
    except ValueError:
        return None, currency


def parse_rating(value: Any) -> Optional[int]:
    """'Three' / 'three' / '3' / 3 -> 3. Anything unrecognised -> None (validator range-checks 1-5)."""
    text = clean_text(value)
    if text is None:
        return None
    text = text.lower()
    if text in RATING_WORDS:
        return RATING_WORDS[text]
    try:
        return int(float(text))
    except ValueError:
        return None


def normalize_url(url: Any, base: Optional[str] = None) -> Optional[str]:
    """Absolute http(s) URL with lowercase scheme/host and no fragment; invalid -> None."""
    url = clean_text(url)
    if url is None:
        return None
    if base:
        url = urljoin(base, url)
    parts = urlparse(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc or " " in url:
        return None
    return urlunparse((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.params, parts.query, ""))


def clean_tags(tags: Any) -> list[str]:
    """Lowercase, trim, drop empties, de-duplicate while keeping order."""
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = re.split(r"[|,]", tags)
    seen, out = set(), []
    for tag in tags:
        tag = clean_text(tag)
        if tag:
            tag = tag.lower()
            if tag not in seen:
                seen.add(tag)
                out.append(tag)
    return out


def normalize_date(value: Any) -> Optional[str]:
    """'March 14, 1879' -> '1879-03-14'. Unparseable -> None (never guessed)."""
    text = clean_text(value)
    if text is None:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def clean_location(value: Any) -> Optional[str]:
    """'in Ulm, Germany' -> 'Ulm, Germany'."""
    text = clean_text(value)
    if text is None:
        return None
    return clean_text(re.sub(r"^in\s+", "", text, flags=re.IGNORECASE))


def clean_record(raw: dict) -> dict:
    """Map one raw scraped dict onto the standardized schema.

    Fields that do not apply to a source stay None (e.g. quotes have no price) - nothing is invented.
    Invalid values are cleaned to None/empty and left for validation.py to reject.
    """
    source_url = normalize_url(raw.get("source_url"))
    price, currency = parse_price(raw.get("price"))
    record_type = clean_text(raw.get("record_type"))
    name = clean_text(raw.get("name_or_title"))
    if record_type == "quote":
        name = strip_wrapping_quotes(name)

    availability = clean_text(raw.get("availability"))
    cleaned = {
        "source": clean_text(raw.get("source")),
        "record_type": record_type.lower() if record_type else None,
        "source_url": source_url,
        "name_or_title": name,
        "category": clean_text(raw.get("category")),
        "price": price,
        "currency": currency if price is not None else None,
        "rating": parse_rating(raw.get("rating")),
        "availability": availability,
        "author": clean_text(raw.get("author")),
        "author_url": normalize_url(raw.get("author_url")),
        "author_born_date": normalize_date(raw.get("author_born_date")),
        "author_born_location": clean_location(raw.get("author_born_location")),
        "tags": clean_tags(raw.get("tags")),
        "description": clean_text(raw.get("description")),
        "scraped_at": clean_text(raw.get("scraped_at")),
        "near_duplicate_of": None,
    }
    # keep exactly the schema columns, in schema order
    return {col: cleaned.get(col) for col in SCHEMA_COLUMNS}

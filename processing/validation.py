"""Record validation. Invalid records are rejected with human-readable reasons."""
from __future__ import annotations

from collections import Counter
from urllib.parse import urlparse

KNOWN_SOURCES = {"Books to Scrape", "Quotes to Scrape"}
RATING_MIN, RATING_MAX = 1, 5

# Fields that must be present, per record type.
REQUIRED_BY_TYPE = {
    "book": ["name_or_title", "source_url", "price"],
    "quote": ["name_or_title", "source_url", "author"],
}


def looks_like_url(url) -> bool:
    if not isinstance(url, str) or " " in url:
        return False
    parts = urlparse(url)
    return parts.scheme in ("http", "https") and bool(parts.netloc) and "." in parts.netloc


def validate_record(rec: dict) -> list[str]:
    """Return a list of problems (empty list = valid)."""
    problems = []
    if rec.get("source") not in KNOWN_SOURCES:
        problems.append("unrecognised_source")
    rtype = rec.get("record_type")
    if rtype not in REQUIRED_BY_TYPE:
        problems.append("unrecognised_record_type")
    for field in REQUIRED_BY_TYPE.get(rtype, ["name_or_title", "source_url"]):
        if rec.get(field) in (None, ""):
            problems.append(f"missing_{field}")
    if rec.get("source_url") and not looks_like_url(rec["source_url"]):
        problems.append("invalid_source_url")
    if rec.get("author_url") and not looks_like_url(rec["author_url"]):
        problems.append("invalid_author_url")
    price = rec.get("price")
    if price is not None and (not isinstance(price, (int, float)) or price < 0):
        problems.append("invalid_price")
    rating = rec.get("rating")
    if rating is not None and not (isinstance(rating, int) and RATING_MIN <= rating <= RATING_MAX):
        problems.append("rating_out_of_range")
    return problems


def validate_all(records: list[dict]) -> tuple[list[dict], list[dict], Counter]:
    """Split records into (valid, rejected, reason_counts). Rejections keep the reasons for the audit trail."""
    valid, rejected, reasons = [], [], Counter()
    for rec in records:
        problems = validate_record(rec)
        if problems:
            reasons.update(problems)
            rejected.append({
                "source": rec.get("source"),
                "name_or_title": rec.get("name_or_title"),
                "source_url": rec.get("source_url"),
                "reasons": problems,
            })
        else:
            valid.append(rec)
    return valid, rejected, reasons

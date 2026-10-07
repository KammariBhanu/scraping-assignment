"""Duplicate detection.

Two stages:
1. EXACT (after normalization) -> removed. Normalization = NFKD, strip accents, casefold,
   punctuation -> space, collapse whitespace. So "Example Book Title", " Example  Book Title "
   and "EXAMPLE BOOK TITLE" produce the same key.
   Keys:  book  = (source, normalized title, price)
          quote = (source, normalized quote text, normalized author)
   When duplicates are removed, empty fields on the kept record are back-filled from the dropped one.
2. NEAR (difflib similarity >= threshold, not identical key) -> FLAGGED, not removed.
   Flagging is deliberate: e.g. same book title with a different price, or a quote with slightly different
   wording, might be distinct records; a human should decide. The later record carries
   `near_duplicate_of` = '<other name> (similarity 0.97)'.
Records from different sources are never considered duplicates of each other (different keys).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher


def normalize_for_matching(value) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]|_", " ", text.casefold())
    return re.sub(r"\s+", " ", text).strip()


def exact_key(rec: dict) -> tuple:
    name = normalize_for_matching(rec.get("name_or_title"))
    if rec.get("record_type") == "quote":
        return (rec.get("source"), name, normalize_for_matching(rec.get("author")))
    return (rec.get("source"), name, rec.get("price"))


def _block_key(rec: dict) -> tuple:
    """Only compare records that could plausibly be near-duplicates (keeps this fast)."""
    name = normalize_for_matching(rec.get("name_or_title"))
    if rec.get("record_type") == "quote":
        return (rec.get("source"), normalize_for_matching(rec.get("author")))
    return (rec.get("source"), name[:1])


@dataclass
class DedupResult:
    records: list = field(default_factory=list)
    exact_removed: int = 0
    near_flagged: int = 0
    removed_by_source: dict = field(default_factory=dict)
    flagged_by_source: dict = field(default_factory=dict)
    examples: list = field(default_factory=list)  # a few examples for the report


def deduplicate(records: list[dict], threshold: float = 0.92) -> DedupResult:
    result = DedupResult()
    kept: dict[tuple, dict] = {}
    for rec in records:
        key = exact_key(rec)
        if key in kept:
            original = kept[key]
            for col, val in rec.items():  # back-fill gaps on the kept record
                if original.get(col) in (None, "", []) and val not in (None, "", []):
                    original[col] = val
            result.exact_removed += 1
            src = rec.get("source")
            result.removed_by_source[src] = result.removed_by_source.get(src, 0) + 1
            if len(result.examples) < 10:
                result.examples.append({"type": "exact_removed", "name": rec.get("name_or_title"),
                                        "source": src, "url": rec.get("source_url")})
        else:
            kept[key] = rec
    unique = list(kept.values())

    blocks: dict[tuple, list[dict]] = {}
    for rec in unique:
        block = blocks.setdefault(_block_key(rec), [])
        norm = normalize_for_matching(rec.get("name_or_title"))
        for earlier_norm, earlier in block:
            if earlier_norm == norm or not norm:
                match_score = 1.0 if earlier_norm == norm else 0.0
            else:
                sm = SequenceMatcher(None, earlier_norm, norm)
                if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                    continue
                match_score = sm.ratio()
            if match_score >= threshold:
                rec["near_duplicate_of"] = f"{earlier.get('name_or_title')} (similarity {match_score:.2f})"
                result.near_flagged += 1
                src = rec.get("source")
                result.flagged_by_source[src] = result.flagged_by_source.get(src, 0) + 1
                if len(result.examples) < 20:
                    result.examples.append({"type": "near_flagged", "name": rec.get("name_or_title"),
                                            "similar_to": earlier.get("name_or_title"),
                                            "similarity": round(match_score, 2), "source": src})
                break
        block.append((norm, rec))
    result.records = unique
    return result

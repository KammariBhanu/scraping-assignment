"""Orchestrates: scrape -> clean -> validate -> deduplicate -> consolidate -> write outputs."""
from __future__ import annotations

import csv
import json
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from config import SCHEMA_COLUMNS, TAG_SEPARATOR, Settings
from processing.cleaning import clean_record
from processing.deduplication import deduplicate
from processing.validation import validate_all
from scrapers.base import HttpClient, ScrapeResult
from scrapers.books_scraper import BooksScraper
from scrapers.quotes_scraper import QuotesScraper

log = logging.getLogger(__name__)

SCRAPERS = {"books": BooksScraper, "quotes": QuotesScraper}


def _scrape_source(name: str, client: HttpClient, settings: Settings) -> ScrapeResult:
    """Run one scraper; a crash in one source never stops the others."""
    try:
        return SCRAPERS[name](client, settings).scrape()
    except Exception as exc:  # defensive: scrapers already handle expected failures
        log.exception("Source '%s' crashed", name)
        res = ScrapeResult(source=name)
        res.add_error(f"Source crashed: {type(exc).__name__}: {exc}")
        return res


def write_csv(records: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SCHEMA_COLUMNS)
        writer.writeheader()
        for rec in records:
            row = dict(rec)
            row["tags"] = TAG_SEPARATOR.join(rec.get("tags") or [])
            writer.writerow({k: ("" if v is None else v) for k, v in row.items()})


def run_pipeline(settings: Settings, sources: list[str], client: HttpClient | None = None) -> dict:
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    client = client or HttpClient(settings)
    out_dir = Path(settings.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_clean, all_rejected = [], []
    per_source, all_errors = {}, []
    reason_totals = Counter()

    for name in sources:
        scraped = _scrape_source(name, client, settings)
        cleaned, clean_failures = [], 0
        for raw in scraped.records:
            try:
                cleaned.append(clean_record(raw))
            except Exception as exc:
                clean_failures += 1
                log.warning("Cleaning failed for a %s record: %s", name, exc)
        valid, rejected, reasons = validate_all(cleaned)
        reason_totals.update(reasons)
        all_clean.extend(valid)
        all_rejected.extend(rejected)
        all_errors.extend(scraped.errors)
        per_source[scraped.source] = {
            "pages_fetched": scraped.pages_fetched,
            "pages_failed": scraped.pages_failed,
            "items_failed_parsing": scraped.items_failed_parsing,
            "detail_requests": scraped.detail_requests,
            "detail_requests_failed": scraped.detail_requests_failed,
            "records_collected": len(scraped.records),
            "records_after_cleaning": len(cleaned),
            "cleaning_failures": clean_failures,
            "rejected_in_validation": len(rejected),
            "rejection_reasons": dict(reasons),
        }
        log.info("%s: collected=%d cleaned=%d rejected=%d", scraped.source,
                 len(scraped.records), len(cleaned), len(rejected))

    dedup = deduplicate(all_clean, settings.near_duplicate_threshold)
    final = dedup.records
    final_by_source = Counter(r["source"] for r in final)
    for src, stats in per_source.items():
        stats["exact_duplicates_removed"] = dedup.removed_by_source.get(src, 0)
        stats["near_duplicates_flagged"] = dedup.flagged_by_source.get(src, 0)
        stats["final_records"] = final_by_source.get(src, 0)

    # Data-quality: null counts per column per source (empty tags count as null)
    quality = {}
    for src in per_source:
        rows = [r for r in final if r["source"] == src]
        quality[src] = {
            col: sum(1 for r in rows if r.get(col) in (None, "", []))
            for col in SCHEMA_COLUMNS
        }

    write_csv(final, out_dir / "final_dataset.csv")
    (out_dir / "rejected_records.json").write_text(
        json.dumps(all_rejected, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {
        "run": {
            "started_at_utc": started_at,
            "execution_time_seconds": round(time.monotonic() - started, 2),
            "sources_requested": sources,
            "settings": settings.as_dict(),
        },
        "totals": {
            "records_collected": sum(s["records_collected"] for s in per_source.values()),
            "records_after_cleaning": sum(s["records_after_cleaning"] for s in per_source.values()),
            "rejected_in_validation": len(all_rejected),
            "exact_duplicates_removed": dedup.exact_removed,
            "near_duplicates_flagged": dedup.near_flagged,
            "final_records": len(final),
        },
        "per_source": per_source,
        "validation_rejection_reasons": dict(reason_totals),
        "duplicate_examples": dedup.examples,
        "null_counts_in_final_dataset": quality,
        "errors_logged": {"count": len(all_errors), "first_50": all_errors[:50]},
    }
    (out_dir / "summary_report.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Done in %.1fs: %d final records -> %s", summary["run"]["execution_time_seconds"],
             len(final), out_dir / "final_dataset.csv")
    return summary

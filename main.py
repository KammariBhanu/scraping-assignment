import argparse
import logging
import sys
from pathlib import Path

from config import Settings
from pipeline import SCRAPERS, run_pipeline


def setup_logging(log_dir: str, level: str) -> Path:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / "scrape.log"
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    console = logging.StreamHandler()
    for h in (file_handler, console):
        h.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    root.addHandler(file_handler)
    root.addHandler(console)
    return log_path


def parse_args(argv=None) -> argparse.Namespace:
    d = Settings()
    p = argparse.ArgumentParser(description="Multi-source scraping & consolidation pipeline")
    p.add_argument("--sources", nargs="+", choices=sorted(SCRAPERS), default=sorted(SCRAPERS))
    p.add_argument("--max-pages", type=int, default=None, help="limit listing pages per source (default: all)")
    p.add_argument("--delay", type=float, default=d.delay, help="min seconds between requests")
    p.add_argument("--workers", type=int, default=d.workers, help="threads for detail/author pages")
    p.add_argument("--timeout", type=float, default=d.timeout)
    p.add_argument("--retries", type=int, default=d.max_retries)
    p.add_argument("--no-details", action="store_true", help="skip book product pages (no category/description)")
    p.add_argument("--no-authors", action="store_true", help="skip quote author pages")
    p.add_argument("--near-duplicate-threshold", type=float, default=d.near_duplicate_threshold)
    p.add_argument("--output-dir", default=d.output_dir)
    p.add_argument("--log-dir", default=d.log_dir)
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_dir, args.log_level)
    settings = Settings(
        timeout=args.timeout, max_retries=args.retries, delay=args.delay, workers=args.workers,
        max_pages=args.max_pages, fetch_details=not args.no_details, fetch_authors=not args.no_authors,
        near_duplicate_threshold=args.near_duplicate_threshold,
        output_dir=args.output_dir, log_dir=args.log_dir,
    )
    summary = run_pipeline(settings, args.sources)
    totals = summary["totals"]
    print(f"\nCollected {totals['records_collected']} | rejected {totals['rejected_in_validation']} | "
          f"duplicates removed {totals['exact_duplicates_removed']} | flagged {totals['near_duplicates_flagged']} | "
          f"final {totals['final_records']}")
    return 0 if totals["final_records"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())

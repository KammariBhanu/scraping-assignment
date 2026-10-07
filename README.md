# Multi-Source Web Scraping & Data Consolidation

A Python pipeline that scrapes **Books to Scrape** and **Quotes to Scrape**, normalizes both into one schema,
cleans and validates the records, detects duplicates, and writes a consolidated CSV plus a JSON summary.

```
Books ─┐
       ├─> Scrape ─> Clean ─> Validate ─> Deduplicate ─> Consolidate ─> output/final_dataset.csv
Quotes ┘                                                              └> output/summary_report.json
```

## Setup & run
- **Python**: 3.10+ (developed on 3.12)
- **Dependencies**: `requests`, `beautifulsoup4` (`requirements.txt`); `pytest` optional (`requirements-dev.txt`)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py                                          # full run (~1,000 book pages + ~50 quote pages; a few minutes)
python main.py --max-pages 2                            # quick smoke test
python main.py --help                                   # all options
python -m unittest discover -s tests -t .               # offline tests (no network needed)
```
Useful flags: `--sources books quotes`, `--max-pages N`, `--delay 0.2`, `--workers 4`, `--retries 4`, `--timeout 15`,
`--no-details`, `--no-authors`, `--near-duplicate-threshold 0.92`, `--output-dir`, `--log-dir`, `--log-level`.
Defaults live in `config.py` (`Settings`).

**Outputs**: `output/final_dataset.csv`, `output/summary_report.json`, `output/rejected_records.json` (audit trail of rejections with reasons), `logs/scrape.log`.

## Project layout
```
main.py              CLI + logging setup          pipeline.py   orchestration + output writing
config.py            Settings + schema columns
scrapers/base.py     HttpClient (retry/backoff/rate limit/robots) + pagination loop
scrapers/books_scraper.py, quotes_scraper.py   site-specific selectors only
processing/cleaning.py, validation.py, deduplication.py   no network / HTML knowledge
tests/               unittest suite with offline HTML fixtures + a fake requests session
```

## Source exploration (Step 1)
| | Books to Scrape | Quotes to Scrape |
|---|---|---|
| Listing | `article.product_pod`, 20 per page, ~50 pages | `div.quote`, 10 per page, ~10 pages |
| Pagination | `li.next a` href (relative; first page is `/`, later pages `catalogue/page-N.html`) | `li.next a` href (`/page/N/`) |
| On listing | title (`h3 a[title]`), price, rating (CSS class word), availability, product URL | quote text, author, tags, author URL |
| Not on listing | **category, description** → product page (breadcrumb, `#product_description + p`) | **birth date/location** → author page |
| Gotchas | Price shows `Â£` if encoding is guessed wrong (we force UTF-8); titles are truncated in link text (full title is in `title` attr); relative links differ between page 1 and page 2+ | Quote text is wrapped in curly quotes; quotes have **no permalink** |

> These observations should be re-confirmed against the live sites when you run the pipeline (see "Known limitations").

## Data model
One row per record. Columns (`config.SCHEMA_COLUMNS`): `source, record_type, source_url, name_or_title, category, price, currency, rating, availability, author, author_url, author_born_date, author_born_location, tags, description, scraped_at, near_duplicate_of`.
- Books → `name_or_title`=title, `price`/`currency`, `rating` (1–5), `category`, `availability`, `description`.
- Quotes → `name_or_title`=quote text, `author`, `author_url`, `author_born_*`, `tags` (pipe-separated in CSV).
- Fields that don't apply are **empty (null)**; nothing is invented. Quotes have no URL of their own, so `source_url` is the listing page they were found on.
- `record_type` (`book`/`quote`) makes per-type validation and dedup rules explicit. A common schema with nullable columns was chosen over two tables because the assignment asks for one consolidated dataset.

## Pagination
Each scraper follows the site's own `li.next a` link until none exists — no page numbers are listed. Guards: visited-URL set (no loops), optional `--max-pages`.
If a page fails (after retries) the loop tries the conventional next URL (`page-N+1`) so one bad page doesn't lose the rest; a 404 on a guessed URL is treated as "end of listing"; two consecutive failures stop that source.

## Cleaning (`processing/cleaning.py`)
Whitespace collapse + NFKC (incl. non-breaking spaces); placeholders (`""`, `N/A`, `null`, `-`…) → null; price → float + currency (`£51.77`/`Â£51.77` → 51.77/GBP); rating word → int; URLs made absolute, scheme/host lowercased, fragment dropped, non-http(s) → null; tags lowercased/trimmed/de-duplicated; wrapping quote marks removed from quote text; birth date → ISO (`1879-03-14`, null if unparseable); `in Ulm, Germany` → `Ulm, Germany`. Cleaning never rejects; it nulls bad values and leaves the decision to validation.

## Validation (`processing/validation.py`)
Rejected (with reasons stored in `rejected_records.json` and counted in the summary) if: unrecognised source/record type; missing required field (book: title, URL, price; quote: text, URL, author); invalid-looking URL; price non-numeric/negative; rating not an integer 1–5. Missing *optional* fields (e.g. category when a product page failed) are kept and visible in the summary's per-column null counts.

## Deduplication (`processing/deduplication.py`)
1. **Normalization key**: NFKD, strip accents, casefold, punctuation→space, collapse whitespace. So `"Example Book Title"`, `" Example  Book Title "`, `"EXAMPLE BOOK TITLE"` and `"Example Book Title!"` all match.
2. **Exact duplicates (removed)**: book = (source, normalized title, price); quote = (source, normalized text, normalized author). First occurrence kept; its empty fields are back-filled from the dropped copy. Price is part of the book key so same-titled books with different prices are not silently merged.
3. **Near duplicates (flagged, not removed)**: `difflib` similarity ≥ 0.92 (configurable) within a block (quotes: same author; books: same first letter). The later record gets `near_duplicate_of = "<other title> (similarity 0.97)"`. Flagged rather than deleted because high similarity ≠ identity (e.g. editions, or same title at a different price), and a wrongly deleted record is unrecoverable.
4. Records from different sources are never duplicates of each other.
Counts of removed/flagged records and examples appear in the summary.

## Error handling & logging
Timeouts, connection errors and 408/425/429/5xx are retried with exponential backoff (+ jitter); other 4xx are not retried. robots.txt is honoured (fails open if unreadable). A global minimum delay between requests (`--delay`) applies across threads. A failed listing page, product page, author page, malformed card, cleaning error, or entire-source crash is logged and skipped; the run continues. Missing HTML elements yield `None`. `logs/scrape.log` records everything; exit code is non-zero only if the final dataset is empty.

## Summary report
`summary_report.json`: per-source pages fetched/failed, detail requests/failures, records collected → after cleaning → rejected → duplicates removed/flagged → final; rejection reasons; duplicate examples; null counts per column; first 50 errors; execution time; settings used.

## Assumptions
Both sites' static HTML is sufficient (no JS rendering needed, so no Playwright/Selenium); book category/description and author birth info count as part of the record and justify the extra requests; the quotes site's listing page is an acceptable `source_url` for quotes.

## Known limitations
- **Verification status**: the unit/integration tests use offline fixtures that mirror the sites' markup; see `AI_USAGE.md` for the status of the live run.
- No checkpoint/resume or incremental scraping; a crash mid-run loses in-memory data.
- Near-duplicate blocking is heuristic (first letter / author), so a typo in the first letter of a book title can evade the fuzzy pass.
- Threads share one rate limiter, so `--workers` mainly overlaps latency, not request rate.

## Production changes I would make
Persist raw HTML/records (checkpointing), incremental runs keyed on URL, scheduling + alerting on failure counts, a database sink, structured (JSON) logs, and contract tests that alert when selectors stop matching.

## AI usage summary
Built with Claude (Anthropic). Details, prompts, and verification in `AI_USAGE.md`.

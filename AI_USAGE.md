# AI Usage

> **Review before submitting:** sections marked **[FILL IN]** need your own input. You should be able to explain every
> part of this code in the interview; read the modules and run the live pipeline first.

## Tool
**Claude (Anthropic), chat interface** — used for essentially the whole implementation.

## What it was used for
- Designing the schema, project structure, and dedup/validation rules.
- Generating the scrapers, `HttpClient` (retry/backoff/rate limit), pagination loop, cleaning/validation/dedup modules, CLI, and tests.
- Drafting `README.md` and this file.

## Representative prompt
"Complete the assignment" (with the assignment PDF attached). Follow-up prompts you used: **[FILL IN]**

## Which parts are AI-assisted
All of it: `scrapers/`, `processing/`, `pipeline.py`, `main.py`, `config.py`, `tests/`, docs.

## Issues found in AI output / during development
- Scaffolding command used shell brace expansion in `sh`, producing a stray directory literally named `{scrapers,processing,...`; found and removed.
- The AI's environment had **no access to the target sites** (requests returned HTTP 403 from the proxy) and no `pytest`. Consequently: tests use `unittest`, and HTML selectors were written from knowledge of the sites' markup and mirrored in test fixtures — **not** checked against live pages by the AI.
- Initial design had non-thread-safe counters updated from worker threads; replaced with a locked `ScrapeResult.incr()`.
- **[FILL IN]** anything else you find on the live run (selector mismatches, encoding, counts that look wrong).

## Verification performed
- 25 offline tests (cleaning, validation, dedup, retry/backoff, pagination incl. failed-page recovery, missing elements, end-to-end CSV/JSON output, one-source-crash isolation) — passing.
- CLI run with the network blocked: each page failure logged, no crash, valid empty outputs written.
- **Live run: [FILL IN]** — run `python main.py` on your machine, then record: books collected (expected 1,000), quotes collected (expected 100), pages fetched/failed, spot-check ≥10 rows against the websites, and confirm `final_dataset.csv`, `summary_report.json`, and `logs/scrape.log` look right.

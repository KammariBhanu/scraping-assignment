import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from config import SCHEMA_COLUMNS, Settings
from pipeline import run_pipeline
from scrapers import books_scraper, quotes_scraper
from scrapers.base import FetchError, HttpClient
from tests.fakes import FakeSession, make_site, listing, book_card


def fast_settings(**kw):
    base = dict(delay=0, backoff_factor=0, max_retries=2, workers=2)
    base.update(kw)
    return Settings(**base)


def client_for(pages, **kw):
    s = fast_settings(**kw)
    return HttpClient(s, session=FakeSession(pages)), s


class HttpClientTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch("scrapers.base.time.sleep"); p.start(); self.addCleanup(p.stop)

    def test_retries_then_succeeds(self):
        client, _ = client_for({"https://a.com/x": [503, requests.Timeout("slow"), "<html>ok</html>"]})
        self.assertIn("ok", client.get_html("https://a.com/x"))

    def test_gives_up_with_fetch_error(self):
        client, _ = client_for({"https://a.com/x": requests.ConnectionError("down")})
        with self.assertRaises(FetchError):
            client.get_html("https://a.com/x")

    def test_404_not_retried(self):
        client, _ = client_for({})
        with self.assertRaises(FetchError) as ctx:
            client.get_html("https://a.com/missing")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(client.session.calls.count("https://a.com/missing"), 1)


class ScraperTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch("scrapers.base.time.sleep"); p.start(); self.addCleanup(p.stop)

    def test_books_pagination_details_and_missing_elements(self):
        client, s = client_for(make_site())
        res = books_scraper.BooksScraper(client, s).scrape()
        self.assertEqual(res.pages_fetched, 2)
        self.assertEqual(len(res.records), 6)
        by_title = {r["name_or_title"]: r for r in res.records}
        self.assertEqual(by_title["Example Book Title"]["category"], "Poetry")
        self.assertEqual(by_title["Example Book Title"]["source_url"],
                         "https://books.toscrape.com/catalogue/example-book_1/index.html")
        self.assertIsNone(by_title["No Price Book"]["price"])           # missing element -> None, no crash
        self.assertIsNone(by_title["Second Book"]["description"])       # no description block
        self.assertIsNone(by_title["Third Book"]["rating"])              # no rating element
        self.assertEqual(res.detail_requests_failed, 1)                  # 500 on third-book detail
        self.assertIsNone(by_title["Third Book"]["category"])            # kept without category

    def test_quotes_pagination_and_author_info(self):
        client, s = client_for(make_site())
        res = quotes_scraper.QuotesScraper(client, s).scrape()
        self.assertEqual(res.pages_fetched, 2)
        self.assertEqual(len(res.records), 4)
        einstein = [r for r in res.records if r["author"] == "Albert Einstein"]
        self.assertTrue(all(r["author_born_date"] == "March 14, 1879" for r in einstein))
        self.assertEqual(client.session.calls.count("https://quotes.toscrape.com/author/Albert-Einstein"), 1)  # fetched once

    def test_max_pages_respected(self):
        client, s = client_for(make_site(), max_pages=1)
        res = books_scraper.BooksScraper(client, s).scrape()
        self.assertEqual(res.pages_fetched, 1)

    def test_failed_page_is_skipped_and_scraping_continues(self):
        pages = make_site()
        pages["https://books.toscrape.com/"] = listing([book_card("P1", "catalogue/p1/index.html")], "catalogue/page-2.html")
        pages["https://books.toscrape.com/catalogue/page-2.html"] = 500
        pages["https://books.toscrape.com/catalogue/page-3.html"] = listing([book_card("P3", "p3/index.html")])
        client, s = client_for(pages, fetch_details=False)
        res = books_scraper.BooksScraper(client, s).scrape()
        self.assertEqual(res.pages_failed, 1)
        self.assertEqual([r["name_or_title"] for r in res.records], ["P1", "P3"])  # page 3 recovered via URL guess

    def test_guess_next_page(self):
        self.assertEqual(books_scraper.guess_next_page("https://books.toscrape.com/catalogue/page-7.html"),
                         "https://books.toscrape.com/catalogue/page-8.html")
        self.assertEqual(quotes_scraper.guess_next_page("https://quotes.toscrape.com/page/9/"),
                         "https://quotes.toscrape.com/page/10/")

    def test_malformed_html_does_not_crash(self):
        pages = {"https://quotes.toscrape.com/": "<html><div class='quote'></div></html>"}
        client, s = client_for(pages, fetch_authors=False)
        res = quotes_scraper.QuotesScraper(client, s).scrape()
        self.assertEqual(len(res.records), 1)  # empty fields -> None, rejected later by validation


class PipelineTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch("scrapers.base.time.sleep"); p.start(); self.addCleanup(p.stop)

    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = fast_settings(output_dir=tmp)
            client = HttpClient(s, session=FakeSession(make_site()))
            summary = run_pipeline(s, ["books", "quotes"], client=client)

            with open(Path(tmp) / "final_dataset.csv", encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(list(rows[0].keys()), SCHEMA_COLUMNS)
            self.assertTrue(all(r["source"] and r["source_url"] for r in rows))

            t = summary["totals"]
            self.assertEqual(t["records_collected"], 10)          # 6 books + 4 quotes
            self.assertEqual(t["rejected_in_validation"], 1)       # book without price
            self.assertEqual(t["exact_duplicates_removed"], 2)     # book title variant + repeated quote
            self.assertEqual(t["near_duplicates_flagged"], 1)      # same title, different price
            self.assertEqual(t["final_records"], len(rows))
            self.assertEqual(t["final_records"], 10 - 1 - 2)
            self.assertEqual(summary["validation_rejection_reasons"], {"missing_price": 1})

            einstein = [r for r in rows if r["author"] == "Albert Einstein"]
            self.assertEqual(einstein[0]["author_born_date"], "1879-03-14")
            self.assertEqual(einstein[0]["author_born_location"], "Ulm, Germany")
            self.assertEqual(einstein[0]["tags"], "change|deep-thoughts")
            books = [r for r in rows if r["record_type"] == "book"]
            self.assertTrue(all(r["price"] and r["currency"] == "GBP" for r in books))
            self.assertTrue(all(r["price"] == "" for r in rows if r["record_type"] == "quote"))  # not invented

            with open(Path(tmp) / "summary_report.json", encoding="utf-8") as fh:
                report = json.load(fh)
            self.assertIn("execution_time_seconds", report["run"])
            self.assertEqual(report["per_source"]["Books to Scrape"]["pages_fetched"], 2)

    def test_one_source_dying_does_not_stop_the_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = fast_settings(output_dir=tmp)
            client = HttpClient(s, session=FakeSession(make_site()))
            with mock.patch.object(books_scraper.BooksScraper, "scrape", side_effect=RuntimeError("boom")):
                summary = run_pipeline(s, ["books", "quotes"], client=client)
            self.assertGreater(summary["per_source"]["Quotes to Scrape"]["final_records"], 0)
            self.assertGreaterEqual(summary["errors_logged"]["count"], 1)


if __name__ == "__main__":
    unittest.main()

import unittest

from processing.cleaning import (clean_record, clean_tags, normalize_date, normalize_url, parse_price,
                                 parse_rating, clean_text, strip_wrapping_quotes)
from processing.deduplication import deduplicate, normalize_for_matching
from processing.validation import validate_record


def rec(**kw):
    base = {"source": "Books to Scrape", "record_type": "book", "source_url": "https://books.toscrape.com/a/index.html",
            "name_or_title": "A Book", "price": 10.0, "rating": 3, "author": None, "author_url": None,
            "category": "Poetry", "tags": [], "near_duplicate_of": None}
    base.update(kw)
    return base


class CleaningTests(unittest.TestCase):
    def test_clean_text(self):
        self.assertEqual(clean_text("  Hello \u00a0\n  world  "), "Hello world")
        for missing in (None, "", "   ", "N/A", "null", "-"):
            self.assertIsNone(clean_text(missing))

    def test_price(self):
        self.assertEqual(parse_price("£51.77"), (51.77, "GBP"))
        self.assertEqual(parse_price("Â£51.77"), (51.77, "GBP"))
        self.assertEqual(parse_price("$1,234.50"), (1234.5, "USD"))
        self.assertEqual(parse_price("free"), (None, None))
        self.assertEqual(parse_price(None), (None, None))

    def test_rating(self):
        self.assertEqual(parse_rating("Three"), 3)
        self.assertEqual(parse_rating("five"), 5)
        self.assertEqual(parse_rating("4"), 4)
        self.assertEqual(parse_rating("9"), 9)  # kept; validator rejects
        self.assertIsNone(parse_rating("lots"))
        self.assertIsNone(parse_rating(None))

    def test_url(self):
        self.assertEqual(normalize_url("HTTPS://Books.ToScrape.com/a#frag"), "https://books.toscrape.com/a")
        self.assertEqual(normalize_url("b/index.html", base="https://x.com/a/"), "https://x.com/a/b/index.html")
        for bad in (None, "", "ftp://x.com", "not a url", "javascript:alert(1)"):
            self.assertIsNone(normalize_url(bad))

    def test_tags_date_quotes(self):
        self.assertEqual(clean_tags([" Love", "love", "", None, "Life "]), ["love", "life"])
        self.assertEqual(normalize_date("March 14, 1879"), "1879-03-14")
        self.assertIsNone(normalize_date("sometime"))
        self.assertEqual(strip_wrapping_quotes("\u201cHi there.\u201d"), "Hi there.")

    def test_clean_record_does_not_invent_data(self):
        r = clean_record({"source": "Quotes to Scrape", "record_type": "quote",
                          "source_url": "https://quotes.toscrape.com/", "name_or_title": "\u201c Hi \u201d",
                          "author": " A ", "tags": ["X"]})
        self.assertIsNone(r["price"]); self.assertIsNone(r["rating"]); self.assertIsNone(r["category"])
        self.assertEqual(r["name_or_title"], "Hi")


class ValidationTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_record(rec()), [])

    def test_rejections(self):
        self.assertIn("missing_price", validate_record(rec(price=None)))
        self.assertIn("rating_out_of_range", validate_record(rec(rating=9)))
        self.assertIn("rating_out_of_range", validate_record(rec(rating=0)))
        self.assertIn("invalid_price", validate_record(rec(price=-1)))
        self.assertIn("invalid_source_url", validate_record(rec(source_url="nope")))
        self.assertIn("missing_source_url", validate_record(rec(source_url=None)))
        self.assertIn("unrecognised_source", validate_record(rec(source="Other")))
        self.assertIn("missing_name_or_title", validate_record(rec(name_or_title="")))

    def test_quote_requires_author_not_price(self):
        q = rec(record_type="quote", source="Quotes to Scrape", price=None, rating=None, author="X")
        self.assertEqual(validate_record(q), [])
        self.assertIn("missing_author", validate_record({**q, "author": None}))


class DedupTests(unittest.TestCase):
    def test_normalization(self):
        variants = ["Example Book Title", " Example  Book Title ", "EXAMPLE BOOK TITLE", "Example Book Title!", "Éxample Book Title"]
        self.assertEqual({normalize_for_matching(v) for v in variants}, {"example book title"})

    def test_exact_removed_and_backfilled(self):
        a = rec(name_or_title="Example Book Title", category=None)
        b = rec(name_or_title=" EXAMPLE BOOK TITLE ", category="Poetry")
        res = deduplicate([a, b])
        self.assertEqual(len(res.records), 1)
        self.assertEqual(res.exact_removed, 1)
        self.assertEqual(res.records[0]["category"], "Poetry")  # back-filled

    def test_same_title_different_price_is_flagged_not_removed(self):
        res = deduplicate([rec(name_or_title="Same", price=10.0), rec(name_or_title="same", price=12.0)])
        self.assertEqual(len(res.records), 2)
        self.assertEqual(res.near_flagged, 1)
        self.assertIn("similarity", res.records[1]["near_duplicate_of"])

    def test_near_duplicate_fuzzy(self):
        a = rec(name_or_title="The Girl Who Played with Fire", price=1.0)
        b = rec(name_or_title="The Girl Who Played With Fire (Millennium 2)", price=2.0)
        c = rec(name_or_title="Totally different", price=3.0)
        res = deduplicate([a, b, c], threshold=0.7)
        self.assertEqual(res.near_flagged, 1)

    def test_sources_never_cross_match(self):
        q = rec(source="Quotes to Scrape", record_type="quote", name_or_title="Same", author="A", price=None)
        b = rec(name_or_title="Same")
        self.assertEqual(len(deduplicate([q, b]).records), 2)


if __name__ == "__main__":
    unittest.main()

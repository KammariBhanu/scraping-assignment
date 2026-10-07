"""Offline test doubles: HTML builders mirroring the sites' markup + a fake requests.Session."""
import requests


def book_card(title, href, price="£10.00", rating="Three", avail="In stock"):
    price_html = f'<p class="price_color">{price}</p>' if price is not None else ""
    rating_html = f'<p class="star-rating {rating}"></p>' if rating else ""
    return (f'<article class="product_pod">{rating_html}<h3><a href="{href}" title="{title}">{title[:10]}...</a></h3>'
            f'<div class="product_price">{price_html}<p class="instock availability">\n  {avail}\n</p></div></article>')


def listing(cards, next_href=None):
    nxt = f'<li class="next"><a href="{next_href}">next</a></li>' if next_href else ""
    return f"<html><body><ol>{''.join(cards)}</ol><ul class='pager'>{nxt}</ul></body></html>"


def book_detail(category, description):
    desc = (f'<div id="product_description" class="sub-header"><h2>Product Description</h2></div><p>{description}</p>'
            if description else "")
    return (f'<html><body><ul class="breadcrumb"><li><a>Home</a></li><li><a>Books</a></li>'
            f'<li><a>{category}</a></li><li class="active">Title</li></ul>{desc}</body></html>')


def quote_block(text, author, tags, author_href=None):
    author_href = author_href or f"/author/{author.replace(' ', '-')}"
    tags_html = "".join(f'<a class="tag" href="/tag/{t}/page/1/">{t}</a>' for t in tags)
    return (f'<div class="quote"><span class="text">\u201c{text}\u201d</span>'
            f'<span>by <small class="author">{author}</small><a href="{author_href}">(about)</a></span>'
            f'<div class="tags">Tags: {tags_html}</div></div>')


def quotes_page(blocks, next_href=None):
    nxt = f'<li class="next"><a href="{next_href}">Next</a></li>' if next_href else ""
    return f"<html><body>{''.join(blocks)}<ul class='pager'>{nxt}</ul></body></html>"


def author_page(born_date, born_location):
    return (f'<html><body><span class="author-born-date">{born_date}</span>'
            f'<span class="author-born-location">{born_location}</span></body></html>')


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code, self.text, self.encoding = status_code, text, None


class FakeSession:
    """pages: {url: text | int status | Exception | list of those (consumed one per call)}."""

    def __init__(self, pages):
        self.pages = {k: (list(v) if isinstance(v, list) else [v]) for k, v in pages.items()}
        self.headers = {}
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResponse(404)
        queue = self.pages.get(url)
        if queue is None:
            return FakeResponse(404)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, int):
            return FakeResponse(item)
        return FakeResponse(200, item)


def make_site():
    """A small two-source 'website' with: pagination, a missing price, a case/whitespace duplicate,
    a near-duplicate, a missing category, mojibake price, and repeated authors."""
    B = "https://books.toscrape.com/"
    Q = "https://quotes.toscrape.com/"
    pages = {
        B: listing([
            book_card("Example Book Title", "catalogue/example-book_1/index.html", "Â£51.77", "Three"),
            book_card("Second Book", "catalogue/second-book_2/index.html", "£12.50", "Five"),
            book_card("No Price Book", "catalogue/no-price_3/index.html", None, "Two"),
        ], "catalogue/page-2.html"),
        B + "catalogue/page-2.html": listing([
            book_card("  EXAMPLE   BOOK TITLE ", "example-book-dup_4/index.html", "£51.77", "three"),
            book_card("Example Book Title!", "example-book-dup_5/index.html", "£49.99", "Four"),
            book_card("Third Book", "third-book_6/index.html", "£7.00", None),
        ]),
        B + "catalogue/example-book_1/index.html": book_detail("Poetry", "A lovely book."),
        B + "catalogue/second-book_2/index.html": book_detail("Travel", None),
        B + "catalogue/no-price_3/index.html": book_detail("Fiction", "x"),
        B + "catalogue/example-book-dup_4/index.html": book_detail("Poetry", "dup"),
        B + "catalogue/example-book-dup_5/index.html": book_detail("Poetry", "variant"),
        B + "catalogue/third-book_6/index.html": 500,  # detail page keeps failing
        Q: quotes_page([
            quote_block("The world as we have created it.", "Albert Einstein", ["change", "Deep-Thoughts", "change"]),
            quote_block("It is our choices.", "J.K. Rowling", ["abilities"]),
        ], "/page/2/"),
        Q + "page/2/": quotes_page([
            quote_block("The world as we have created it.", "Albert Einstein", ["change"]),  # exact dup of page 1
            quote_block("Another one.", "Albert Einstein", []),
        ]),
        Q + "author/Albert-Einstein": author_page("March 14, 1879", "in Ulm, Germany"),
        Q + "author/J.K.-Rowling": author_page("July 31, 1965", "in Yate, South Gloucestershire, England, The United Kingdom"),
    }
    return pages

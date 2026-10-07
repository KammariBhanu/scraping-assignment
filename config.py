"""Central configuration: runtime settings and the standardized output schema."""
from dataclasses import dataclass, asdict
from typing import Optional

# Final CSV column order (the common data model for both sources).
SCHEMA_COLUMNS = [
    "source",
    "record_type",
    "source_url",
    "name_or_title",
    "category",
    "price",
    "currency",
    "rating",
    "availability",
    "author",
    "author_url",
    "author_born_date",
    "author_born_location",
    "tags",
    "description",
    "scraped_at",
    "near_duplicate_of",
]

# Separator used when a list (tags) is written into a single CSV cell.
TAG_SEPARATOR = "|"


@dataclass
class Settings:
    user_agent: str = "multi-source-scraping-assignment/1.0 (educational; polite crawler)"
    timeout: float = 15.0            # seconds per request
    max_retries: int = 4             # retries after the first attempt
    backoff_factor: float = 1.0      # sleep = factor * 2**(attempt-1) seconds
    delay: float = 0.2               # minimum gap between ANY two requests (global rate limit)
    workers: int = 4                 # threads for detail/author page fetches
    max_pages: Optional[int] = None  # cap on listing pages per source (None = all)
    fetch_details: bool = True       # books: visit product pages for category/description
    fetch_authors: bool = True       # quotes: visit author pages for birth info
    respect_robots: bool = True
    near_duplicate_threshold: float = 0.92
    output_dir: str = "output"
    log_dir: str = "logs"

    def as_dict(self):
        return asdict(self)

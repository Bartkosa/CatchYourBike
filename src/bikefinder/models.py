from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Listing:
    listing_id: str
    url: str
    title: str
    body: str
    posted_at: str | None
    price: str | None
    location: str | None
    image_urls: list[str] = field(default_factory=list)
    search_name: str = ""  # YAML search `name`
    source: str = ""  # marketplace id, e.g. subito (composite listing_id prefix)
    # Which paginated SERP URL (order=datedesc&o=... + ps/pe) produced this listing.
    # Used for per-page crawl diagnostics/logging.
    search_page_url: str | None = None

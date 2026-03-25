from __future__ import annotations

from datetime import datetime
from typing import Protocol

from bikefinder.config import AppConfig
from bikefinder.models import Listing


class ListingSource(Protocol):
    """One marketplace: fetch normalized listings and parse site-specific timestamps."""

    source_id: str
    display_name: str

    def fetch_search_pages(
        self,
        cfg: AppConfig,
        search_url: str,
        search_name: str,
        *,
        start_page_index: int = 0,
    ) -> tuple[list[Listing], bool]:
        """Return (listings, reached_end). Listing IDs must be unique across all sources."""

    def parse_posted_at(self, value: str | None) -> datetime | None:
        """Parse listing post time for cutoff filtering; timezone-aware UTC preferred."""

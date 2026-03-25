from __future__ import annotations

from datetime import datetime, timezone

from bikefinder.config import AppConfig
from bikefinder.models import Listing
from bikefinder.sources.listing_ids import with_source_prefix
from bikefinder.sources.subito.client import fetch_search_pages as _fetch_subito_raw


class SubitoSource:
    source_id = "subito"
    display_name = "Subito.it"

    def fetch_search_pages(
        self,
        cfg: AppConfig,
        search_url: str,
        search_name: str,
        *,
        start_page_index: int = 0,
    ) -> tuple[list[Listing], bool]:
        listings, reached_end = _fetch_subito_raw(
            cfg,
            search_url,
            search_name,
            start_page_index=start_page_index,
        )
        return [with_source_prefix(L, self.source_id) for L in listings], reached_end

    def parse_posted_at(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

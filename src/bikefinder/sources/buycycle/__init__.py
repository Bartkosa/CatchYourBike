from __future__ import annotations

from datetime import datetime, timezone

from bikefinder.config import AppConfig
from bikefinder.models import Listing
from bikefinder.sources.buycycle.client import fetch_search_pages as _fetch_buycycle_raw
from bikefinder.sources.listing_ids import with_source_prefix


class BuycycleSource:
    source_id = "buycycle"
    display_name = "buycycle"

    def fetch_search_pages(
        self,
        cfg: AppConfig,
        search_url: str,
        search_name: str,
        *,
        start_page_index: int = 0,
    ) -> tuple[list[Listing], bool]:
        listings, reached_end = _fetch_buycycle_raw(
            cfg,
            search_url,
            search_name,
            start_page_index=start_page_index,
        )
        return [with_source_prefix(L, self.source_id) for L in listings], reached_end

    def parse_posted_at(self, value: str | None) -> datetime | None:
        if not value:
            return None
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except ValueError:
            return None

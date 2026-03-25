from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bikefinder.config import AppConfig
from bikefinder.models import Listing
from bikefinder.sources.kleinanzeigen.client import fetch_search_pages as _fetch_kleinanzeigen_raw
from bikefinder.sources.kleinanzeigen.serp_html import _parse_posted_at_raw
from bikefinder.sources.listing_ids import with_source_prefix

_BERLIN = ZoneInfo("Europe/Berlin")


class KleinanzeigenSource:
    source_id = "kleinanzeigen"
    display_name = "Kleinanzeigen"

    def fetch_search_pages(
        self,
        cfg: AppConfig,
        search_url: str,
        search_name: str,
        *,
        start_page_index: int = 0,
    ) -> tuple[list[Listing], bool]:
        listings, reached_end = _fetch_kleinanzeigen_raw(
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
        if s[0].isdigit() or s.startswith("20"):
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                pass
        now = datetime.now(tz=_BERLIN)
        iso = _parse_posted_at_raw(s, now=now)
        if iso:
            try:
                return datetime.fromisoformat(iso).astimezone(timezone.utc)
            except ValueError:
                pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

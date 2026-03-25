from __future__ import annotations

from datetime import datetime, timezone

from bikefinder.config import AppConfig
from bikefinder.models import Listing
from bikefinder.sources.listing_ids import with_source_prefix
from bikefinder.sources.wallapop.client import fetch_search_pages as _fetch_wallapop_raw


class WallapopSource:
    source_id = "wallapop"
    display_name = "Wallapop"

    def fetch_search_pages(
        self,
        cfg: AppConfig,
        search_url: str,
        search_name: str,
        *,
        start_page_index: int = 0,
    ) -> tuple[list[Listing], bool]:
        listings, reached_end = _fetch_wallapop_raw(
            cfg,
            search_url,
            search_name,
            start_page_index=start_page_index,
        )
        return [with_source_prefix(x, self.source_id) for x in listings], reached_end

    def parse_posted_at(self, value: str | None) -> datetime | None:
        if not value:
            return None
        # Wallapop may return unix timestamps as seconds/milliseconds.
        if value.isdigit():
            try:
                raw = int(value)
                if raw > 10_000_000_000:
                    raw = raw / 1000.0
                return datetime.fromtimestamp(raw, tz=timezone.utc)
            except (ValueError, OSError):
                pass
        # Wallapop can return full ISO timestamps like 2026-03-19T10:00:00+00:00.
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(value, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

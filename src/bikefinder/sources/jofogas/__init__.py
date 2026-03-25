from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bikefinder.config import AppConfig
from bikefinder.models import Listing
from bikefinder.sources.jofogas.client import fetch_search_pages as _fetch_jofogas_raw
from bikefinder.sources.listing_ids import with_source_prefix

_BUDAPEST = ZoneInfo("Europe/Budapest")


class JofogasSource:
    source_id = "jofogas"
    display_name = "Jófogás"

    def fetch_search_pages(
        self,
        cfg: AppConfig,
        search_url: str,
        search_name: str,
        *,
        start_page_index: int = 0,
    ) -> tuple[list[Listing], bool]:
        listings, reached_end = _fetch_jofogas_raw(
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
                return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(
                    timezone.utc
                )
            except ValueError:
                pass
        try:
            ts = int(s)
            if ts > 0:
                return datetime.fromtimestamp(ts, tz=timezone.utc)
        except ValueError:
            pass
        now = datetime.now(tz=_BUDAPEST)
        low = s.lower()
        if low.startswith("ma,"):
            try:
                rest = s.split(",", 1)[1].strip()
                hh, mm = rest.split(":")
                dt = now.replace(
                    hour=int(hh),
                    minute=int(mm),
                    second=0,
                    microsecond=0,
                )
                return dt.astimezone(timezone.utc)
            except (ValueError, IndexError):
                pass
        if low.startswith("tegnap,"):
            try:
                rest = s.split(",", 1)[1].strip()
                hh, mm = rest.split(":")
                d = (now - timedelta(days=1)).date()
                dt = datetime(
                    d.year,
                    d.month,
                    d.day,
                    int(hh),
                    int(mm),
                    tzinfo=_BUDAPEST,
                )
                return dt.astimezone(timezone.utc)
            except (ValueError, IndexError):
                pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

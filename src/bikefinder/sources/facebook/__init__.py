from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from bikefinder.config import AppConfig
from bikefinder.models import Listing
from bikefinder.sources.facebook.client import fetch_search_pages as _fetch_facebook_raw
from bikefinder.sources.listing_ids import with_source_prefix

_RELATIVE_RE = re.compile(
    r"^(\d+)\s*"
    r"(minuti?|minutes?|mins?|ore?|hours?|hrs?|giorni?|days?|settimane?|weeks?)"
    r"\s*(fa|ago)?\.?$",
    re.IGNORECASE,
)


class FacebookSource:
    source_id = "facebook"
    display_name = "Facebook Marketplace"

    def fetch_search_pages(
        self,
        cfg: AppConfig,
        search_url: str,
        search_name: str,
        *,
        start_page_index: int = 0,
    ) -> tuple[list[Listing], bool]:
        listings, reached_end = _fetch_facebook_raw(
            cfg,
            search_url,
            search_name,
            start_page_index=start_page_index,
        )
        return [with_source_prefix(x, self.source_id) for x in listings], reached_end

    def parse_posted_at(self, value: str | None) -> datetime | None:
        if not value:
            return None
        v = value.strip()
        if v.isdigit():
            try:
                raw = int(v)
                if raw > 10_000_000_000:
                    raw = raw / 1000.0
                return datetime.fromtimestamp(raw, tz=timezone.utc)
            except (ValueError, OSError):
                pass
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(v, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        m = _RELATIVE_RE.match(v)
        if m:
            n = int(m.group(1))
            unit = m.group(2).lower()
            now = datetime.now(timezone.utc)
            if unit.startswith("min"):
                return now - timedelta(minutes=n)
            if unit.startswith("or") or unit.startswith("hour") or unit.startswith("hr"):
                return now - timedelta(hours=n)
            if unit.startswith("gior") or unit.startswith("day"):
                return now - timedelta(days=n)
            if unit.startswith("sett") or unit.startswith("week"):
                return now - timedelta(weeks=n)
        return None

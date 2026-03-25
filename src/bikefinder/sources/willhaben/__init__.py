from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bikefinder.config import AppConfig
from bikefinder.models import Listing
from bikefinder.sources.listing_ids import with_source_prefix
from bikefinder.sources.willhaben.client import fetch_search_pages as _fetch_willhaben_raw

_VIENNA = ZoneInfo("Europe/Vienna")


def _parse_posted_at_raw(
    raw: str | None,
    *,
    now: datetime,
) -> str | None:
    """German SERP-style chip → ISO 8601 with Vienna offset, or None."""
    if not raw:
        return None
    s = " ".join(raw.replace("\xa0", " ").split()).strip()
    if not s:
        return None

    m = re.match(
        r"Heute,\s*(\d{1,2}):(\d{2})\s*Uhr\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        d = now.date()
        hh, mm = int(m.group(1)), int(m.group(2))
        dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=_VIENNA)
        return dt.isoformat()

    m = re.match(
        r"Gestern,\s*(\d{1,2}):(\d{2})\s*Uhr\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        d = now.date() - timedelta(days=1)
        hh, mm = int(m.group(1)), int(m.group(2))
        dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=_VIENNA)
        return dt.isoformat()

    m = re.match(
        r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:,\s*(\d{1,2}):(\d{2})\s*Uhr)?\s*$",
        s,
    )
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if m.group(4):
            hh, minute = int(m.group(4)), int(m.group(5))
        else:
            hh, minute = 12, 0
        dt = datetime(year, month, day, hh, minute, tzinfo=_VIENNA)
        return dt.isoformat()

    return None


class WillhabenSource:
    source_id = "willhaben"
    display_name = "willhaben"

    def fetch_search_pages(
        self,
        cfg: AppConfig,
        search_url: str,
        search_name: str,
        *,
        start_page_index: int = 0,
    ) -> tuple[list[Listing], bool]:
        listings, reached_end = _fetch_willhaben_raw(
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
        now = datetime.now(tz=_VIENNA)
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

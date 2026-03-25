from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bikefinder.config import AppConfig
from bikefinder.models import Listing
from bikefinder.sources.kupujemprodajem.client import fetch_search_pages as _fetch_kp_raw
from bikefinder.sources.listing_ids import with_source_prefix

_BELGRADE = ZoneInfo("Europe/Belgrade")


def _parse_posted_at_raw(
    raw: str | None,
    *,
    now: datetime,
) -> str | None:
    """KP chips (Serbian) → ISO 8601 with Belgrade offset, or pass-through for dates."""
    if not raw:
        return None
    s = " ".join(raw.replace("\xa0", " ").split()).strip()
    if not s:
        return None

    low = s.lower()
    if low == "danas":
        d = now.date()
        dt = datetime(d.year, d.month, d.day, 12, 0, tzinfo=_BELGRADE)
        return dt.isoformat()

    if low in ("juče", "juce", "jučer", "jucer"):
        d = now.date() - timedelta(days=1)
        dt = datetime(d.year, d.month, d.day, 12, 0, tzinfo=_BELGRADE)
        return dt.isoformat()

    m = re.match(
        r"(\d{1,2})\.(\d{1,2})\.(\d{4})\.?(?:\s+(\d{1,2}):(\d{2}))?\s*$",
        s,
    )
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if m.group(4):
            hh, minute = int(m.group(4)), int(m.group(5))
        else:
            hh, minute = 12, 0
        dt = datetime(year, month, day, hh, minute, tzinfo=_BELGRADE)
        return dt.isoformat()

    return None


class KupujemProdajemSource:
    source_id = "kupujemprodajem"
    display_name = "KupujemProdajem"

    def fetch_search_pages(
        self,
        cfg: AppConfig,
        search_url: str,
        search_name: str,
        *,
        start_page_index: int = 0,
    ) -> tuple[list[Listing], bool]:
        listings, reached_end = _fetch_kp_raw(
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
        now = datetime.now(tz=_BELGRADE)
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

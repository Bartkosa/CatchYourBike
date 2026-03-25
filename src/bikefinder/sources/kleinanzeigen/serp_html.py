from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bikefinder.models import Listing

_BERLIN = ZoneInfo("Europe/Berlin")

# One SERP card (may include nested tags; we only need the outer article).
_ADITEM_BLOCK_RE = re.compile(
    r'<article\s+[^>]*\baditem\b[^>]*>(.*?)</article>',
    re.DOTALL | re.IGNORECASE,
)
_DATA_ADID_RE = re.compile(r'data-adid\s*=\s*"(\d+)"', re.IGNORECASE)
_DATA_HREF_RE = re.compile(r'data-href\s*=\s*"([^"]+)"', re.IGNORECASE)
_BREADCRUMB_RE = re.compile(
    r'class="breadcrump-summary"[^>]*>\s*(\d+)\s*-\s*(\d+)\s+von\s+([\d.]+)\s+Ergebnissen',
    re.IGNORECASE,
)
_REL_NEXT_RE = re.compile(
    r'<link\s+rel="next"[^>]*href="([^"]+)"', re.IGNORECASE
)


def _strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return " ".join(text.split())


def _first_match(pattern: re.Pattern[str], html: str, group: int = 1) -> str | None:
    m = pattern.search(html)
    return m.group(group).strip() if m else None


def _extract_by_class(block: str, class_name: str) -> str | None:
    """First ``div`` or ``p`` whose class list contains ``class_name`` (one nesting level)."""
    for tag in ("div", "p"):
        m = re.search(
            rf'<{tag}[^>]*class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(.*?)</{tag}>',
            block,
            re.DOTALL | re.IGNORECASE,
        )
        if m:
            return m.group(1)
    return None


def _img_src(block: str) -> str | None:
    m = re.search(r'<img[^>]+src="([^"]+)"', block, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _ld_json_image_url(block: str) -> str | None:
    for m in re.finditer(
        r'<script\s+type="application/ld\+json"\s*>(.*?)</script>',
        block,
        re.DOTALL | re.IGNORECASE,
    ):
        raw = m.group(1).strip()
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            u = data.get("contentUrl")
            if isinstance(u, str) and u.startswith("http"):
                return u
    return None


def _parse_posted_at_raw(
    raw: str | None,
    *,
    now: datetime,
) -> str | None:
    """German SERP chip → ISO 8601 with Berlin offset, or None."""
    if not raw:
        return None
    s = _strip_tags(raw).strip()
    if not s:
        return None

    m = re.match(
        r"Heute,\s*(\d{1,2}):(\d{2})\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        d = now.date()
        hh, mm = int(m.group(1)), int(m.group(2))
        dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=_BERLIN)
        return dt.isoformat()

    m = re.match(
        r"Gestern,\s*(\d{1,2}):(\d{2})\s*$",
        s,
        re.IGNORECASE,
    )
    if m:
        d = now.date() - timedelta(days=1)
        hh, mm = int(m.group(1)), int(m.group(2))
        dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=_BERLIN)
        return dt.isoformat()

    m = re.match(
        r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:,\s*(\d{1,2}):(\d{2}))?\s*$",
        s,
    )
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if m.group(4):
            hh, minute = int(m.group(4)), int(m.group(5))
        else:
            hh, minute = 12, 0
        dt = datetime(year, month, day, hh, minute, tzinfo=_BERLIN)
        return dt.isoformat()

    return None


def _parse_aditem(
    block_outer: str,
    search_name: str,
    *,
    now: datetime,
) -> Listing | None:
    head = block_outer[:800]
    adid = _first_match(_DATA_ADID_RE, head)
    if not adid:
        return None
    href = _first_match(_DATA_HREF_RE, head)
    if not href:
        m = re.search(r'href="(/s-anzeige/[^"]+)"', block_outer, re.IGNORECASE)
        href = m.group(1) if m else None
    if not href:
        return None
    if href.startswith("/"):
        url = "https://www.kleinanzeigen.de" + href
    else:
        url = href

    title_m = re.search(
        r'<a[^>]+class="[^"]*ellipsis[^"]*"[^>]*href="[^"]*"[^>]*>([^<]+)</a>',
        block_outer,
        re.IGNORECASE,
    )
    title = title_m.group(1).strip() if title_m else ""

    desc_seg = _extract_by_class(block_outer, "aditem-main--middle--description")
    body = _strip_tags(desc_seg) if desc_seg else ""

    price_seg = _extract_by_class(block_outer, "aditem-main--middle--price-shipping--price")
    price = _strip_tags(price_seg) if price_seg else None

    loc_seg = _extract_by_class(block_outer, "aditem-main--top--left")
    location = _strip_tags(loc_seg) if loc_seg else None

    tr_seg = _extract_by_class(block_outer, "aditem-main--top--right")
    posted_at = _parse_posted_at_raw(tr_seg, now=now)

    img = _ld_json_image_url(block_outer) or _img_src(block_outer)
    images = [img] if img else []

    if not title:
        # ld+json title as fallback
        for m in re.finditer(
            r'<script\s+type="application/ld\+json"\s*>(.*?)</script>',
            block_outer,
            re.DOTALL | re.IGNORECASE,
        ):
            try:
                data = json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and isinstance(data.get("title"), str):
                title = data["title"].strip()
                break

    return Listing(
        listing_id=adid,
        url=url,
        title=title or adid,
        body=body,
        posted_at=posted_at,
        price=price,
        location=location,
        image_urls=images,
        search_name=search_name,
    )


def listings_from_search_html(
    html: str,
    search_name: str,
    *,
    now: datetime | None = None,
) -> tuple[list[Listing], int, int]:
    """Parse Kleinanzeigen SRP HTML.

    Returns ``(listings, total_results, total_pages)``. ``total_pages`` uses the
    breadcrumb range (e.g. 1–25) as page size when available; otherwise 25.
    """
    ref = now or datetime.now(tz=_BERLIN)
    listings: list[Listing] = []
    seen: set[str] = set()
    for m in _ADITEM_BLOCK_RE.finditer(html):
        full = m.group(0)
        parsed = _parse_aditem(full, search_name, now=ref)
        if not parsed or parsed.listing_id in seen:
            continue
        seen.add(parsed.listing_id)
        listings.append(parsed)

    total_results = 0
    per_page = 25
    bm = _BREADCRUMB_RE.search(html)
    if bm:
        low = int(bm.group(1))
        high = int(bm.group(2))
        total_results = int(bm.group(3).replace(".", ""))
        per_page = max(1, high - low + 1)

    if total_results > 0:
        total_pages = max(1, (total_results + per_page - 1) // per_page)
    else:
        # Unknown total; client should use ``serp_has_next_page`` to stop pagination.
        total_pages = 10**9

    return listings, total_results, total_pages


def serp_has_next_page(html: str) -> bool:
    return bool(_REL_NEXT_RE.search(html))

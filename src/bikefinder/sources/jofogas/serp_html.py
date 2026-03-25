from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any

from bikefinder.models import Listing

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(?P<json>.*?)</script>',
    re.DOTALL,
)

_REL_NEXT_RE = re.compile(
    r'<link\s+rel="next"[^>]*href="([^"]+)"',
    re.IGNORECASE,
)


def extract_next_data_props(html: str) -> dict[str, Any] | None:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group("json"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    props = data.get("props")
    if not isinstance(props, dict):
        return None
    pp = props.get("pageProps")
    return pp if isinstance(pp, dict) else None


def _strip_html_body(raw: str | None) -> str:
    if not raw or not isinstance(raw, str):
        return ""
    s = unescape(raw)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    return " ".join(s.split()).strip()


def _posted_iso_from_list_time(list_time: Any) -> str | None:
    if not isinstance(list_time, dict):
        return None
    raw = list_time.get("value")
    if raw is None:
        return None
    try:
        ts = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _price_label(price: Any) -> str | None:
    if not isinstance(price, dict):
        return None
    lab = str(price.get("label") or "").strip()
    return lab or None


def _region_label(region: Any) -> str | None:
    if not isinstance(region, dict):
        return None
    lab = str(region.get("label") or "").strip()
    return lab or None


def _best_image_url(img: dict[str, Any]) -> str | None:
    vars_ = img.get("image_size_variations")
    if isinstance(vars_, list):
        order = ("620x620aspect", "images", "bigthumbs")
        by_type: dict[str, str] = {}
        for v in vars_:
            if not isinstance(v, dict):
                continue
            t = str(v.get("type") or "").strip()
            u = str(v.get("url") or "").strip()
            if t and u.startswith("http"):
                by_type[t] = u
        for t in order:
            if t in by_type:
                return by_type[t]
    u = str(img.get("url") or "").strip()
    return u if u.startswith("http") else None


def _image_urls(images: Any) -> list[str]:
    if not isinstance(images, list):
        return []
    out: list[str] = []
    for img in images:
        if not isinstance(img, dict):
            continue
        u = _best_image_url(img)
        if u:
            out.append(u)
    return out


def _listing_from_ad(ad: dict[str, Any], search_name: str) -> Listing | None:
    raw_id = ad.get("list_id")
    if raw_id is None:
        return None
    try:
        lid = int(raw_id)
    except (TypeError, ValueError):
        return None
    url = str(ad.get("url") or "").strip()
    if not url.startswith("http"):
        return None
    title = str(ad.get("subject") or "").strip() or str(lid)
    body = _strip_html_body(ad.get("body") if isinstance(ad.get("body"), str) else None)
    posted = _posted_iso_from_list_time(ad.get("list_time"))
    price = _price_label(ad.get("price"))
    loc = _region_label(ad.get("region"))
    imgs = _image_urls(ad.get("images"))
    return Listing(
        listing_id=str(lid),
        url=url,
        title=title,
        body=body,
        posted_at=posted,
        price=price,
        location=loc,
        image_urls=imgs,
        search_name=search_name,
    )


def listings_from_search_html(
    html: str,
    search_name: str,
) -> tuple[list[Listing], int, int]:
    """Parse Jófogás category SERP from Next.js ``__NEXT_DATA__``.

    Returns ``(listings, total_results, total_pages)``.
    """
    pp = extract_next_data_props(html)
    if not pp:
        return [], 0, 1

    ad_list = pp.get("adList")
    if not isinstance(ad_list, dict):
        return [], 0, 1

    raw_ads = ad_list.get("ads")
    if not isinstance(raw_ads, list):
        return [], 0, 1

    listings: list[Listing] = []
    for item in raw_ads:
        if not isinstance(item, dict):
            continue
        parsed = _listing_from_ad(item, search_name)
        if parsed:
            listings.append(parsed)

    total_results = int(ad_list.get("search_total") or 0)
    pager = ad_list.get("pager")
    total_pages = 1
    if isinstance(pager, dict):
        pc = int(pager.get("page_count") or 0)
        if pc > 0:
            total_pages = pc

    if total_results > 0 and total_pages <= 1 and listings:
        ps = 0
        if isinstance(pager, dict):
            ps = int(pager.get("page_size") or 0)
        per_page = max(1, ps or len(listings))
        total_pages = max(1, math.ceil(total_results / per_page))

    return listings, total_results, max(1, total_pages)


def serp_has_next_page(html: str) -> bool:
    return bool(_REL_NEXT_RE.search(html))

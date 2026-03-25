from __future__ import annotations

import json
import math
import re
from json import JSONDecoder
from typing import Any

from bikefinder.models import Listing

_NEXT_MARKER = '<script id="__NEXT_DATA__" type="application/json">'


def _as_list(x: Any) -> list[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        return [x]
    return []


def _attr_map(ad: dict[str, Any]) -> dict[str, str]:
    raw = (ad.get("attributes") or {}).get("attribute")
    out: dict[str, str] = {}
    for item in _as_list(raw):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        values = item.get("values")
        if not name or not isinstance(values, list) or not values:
            continue
        v0 = values[0]
        if isinstance(v0, str):
            out[str(name)] = v0
    return out


def _seo_detail_url(ad: dict[str, Any]) -> str | None:
    raw = (ad.get("contextLinkList") or {}).get("contextLink")
    for link in _as_list(raw):
        if not isinstance(link, dict):
            continue
        if link.get("id") != "seoSelfLink":
            continue
        rp = link.get("relativePath")
        if not isinstance(rp, str) or not rp.startswith("/atverz/"):
            continue
        return "https://www.willhaben.at/iad" + rp[len("/atverz") :]
    return None


def _image_urls(ad: dict[str, Any]) -> list[str]:
    raw = (ad.get("advertImageList") or {}).get("advertImage")
    urls: list[str] = []
    for im in _as_list(raw):
        if not isinstance(im, dict):
            continue
        u = im.get("mainImageUrl")
        if isinstance(u, str) and u.startswith("http"):
            urls.append(u)
    return urls


def _advert_to_listing(ad: dict[str, Any], search_name: str) -> Listing | None:
    ad_id = ad.get("id")
    if not isinstance(ad_id, str) or not ad_id.strip():
        return None
    url = _seo_detail_url(ad)
    if not url:
        return None
    attrs = _attr_map(ad)
    title = ad.get("description")
    if not isinstance(title, str):
        title = ad_id
    body = attrs.get("BODY_DYN", "") or ""
    price = attrs.get("PRICE_FOR_DISPLAY")
    posted = attrs.get("CHANGED_String")
    loc_parts = [attrs.get("POSTCODE", "").strip(), attrs.get("LOCATION", "").strip()]
    location = " ".join(p for p in loc_parts if p) or None
    images = _image_urls(ad)
    return Listing(
        listing_id=ad_id.strip(),
        url=url,
        title=title.strip() or ad_id,
        body=body.strip(),
        posted_at=posted if posted else None,
        price=price,
        location=location,
        image_urls=images,
        search_name=search_name,
    )


def _parse_next_data(html: str) -> dict[str, Any] | None:
    i = html.find(_NEXT_MARKER)
    if i < 0:
        return None
    tail = html[i + len(_NEXT_MARKER) :].lstrip()
    try:
        data, _end = JSONDecoder().raw_decode(tail)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def listings_from_search_html(html: str, search_name: str) -> tuple[list[Listing], int, int]:
    """Parse willhaben SRP from embedded ``__NEXT_DATA__``.

    Returns ``(listings, total_results, total_pages)``.
    """
    data = _parse_next_data(html)
    if not data:
        return [], 0, 1

    try:
        sr = data["props"]["pageProps"]["searchResult"]
        if not isinstance(sr, dict):
            return [], 0, 1
    except (KeyError, TypeError):
        return [], 0, 1

    raw_list = sr.get("advertSummaryList")
    adverts: list[Any] = []
    if isinstance(raw_list, dict):
        adv = raw_list.get("advertSummary")
        adverts = _as_list(adv)
    elif isinstance(raw_list, list):
        adverts = raw_list

    listings: list[Listing] = []
    seen: set[str] = set()
    for ad in adverts:
        if not isinstance(ad, dict):
            continue
        parsed = _advert_to_listing(ad, search_name)
        if not parsed or parsed.listing_id in seen:
            continue
        seen.add(parsed.listing_id)
        listings.append(parsed)

    rows_found = int(sr.get("rowsFound") or 0)
    rows_ret = int(sr.get("rowsReturned") or 0)
    per_page = rows_ret if rows_ret > 0 else max(1, len(listings))

    if rows_found > 0:
        total_pages = max(1, math.ceil(rows_found / per_page))
    else:
        total_pages = 10**9

    return listings, rows_found, total_pages


_REL_NEXT_RE = re.compile(
    r'<link\s+rel="next"[^>]*href="([^"]+)"',
    re.IGNORECASE,
)


def serp_has_next_page(html: str) -> bool:
    return bool(_REL_NEXT_RE.search(html))

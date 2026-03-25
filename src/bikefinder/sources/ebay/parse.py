from __future__ import annotations

from typing import Any

from bikefinder.models import Listing


def _image_urls_from_item(raw: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    img = raw.get("image")
    if isinstance(img, dict):
        u = img.get("imageUrl")
        if isinstance(u, str) and u.strip():
            urls.append(u.strip())
    thumbs = raw.get("thumbnailImages")
    if isinstance(thumbs, list):
        for t in thumbs:
            if isinstance(t, dict):
                u = t.get("imageUrl")
                if isinstance(u, str) and u.strip() and u.strip() not in urls:
                    urls.append(u.strip())
    return urls


def _location_from_item(raw: dict[str, Any]) -> str | None:
    loc = raw.get("itemLocation")
    if isinstance(loc, dict):
        parts: list[str] = []
        for key in ("city", "stateOrProvince", "postalCode", "country"):
            v = loc.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
        if parts:
            return ", ".join(parts)
    return None


def _price_str(raw: dict[str, Any]) -> str | None:
    p = raw.get("price")
    if not isinstance(p, dict):
        return None
    val = p.get("value")
    cur = p.get("currency")
    if val is None:
        return None
    s = str(val).strip()
    if isinstance(cur, str) and cur.strip():
        return f"{s} {cur.strip()}"
    return s


def parse_ebay_item(raw: dict[str, Any], search_name: str) -> Listing | None:
    item_id = raw.get("itemId")
    if not isinstance(item_id, str) or not item_id.strip():
        return None
    url = raw.get("itemWebUrl")
    if not isinstance(url, str) or not url.strip():
        return None
    title = raw.get("title")
    title_s = title.strip() if isinstance(title, str) else ""
    posted = raw.get("itemOriginDate")
    posted_s = posted.strip() if isinstance(posted, str) else None

    return Listing(
        listing_id=item_id.strip(),
        url=url.strip(),
        title=title_s or "(no title)",
        body="",
        posted_at=posted_s,
        price=_price_str(raw),
        location=_location_from_item(raw),
        image_urls=_image_urls_from_item(raw),
        search_name=search_name,
    )


def listings_from_ebay_search_response(
    payload: dict[str, Any], search_name: str
) -> list[Listing]:
    summaries = payload.get("itemSummaries")
    if not isinstance(summaries, list):
        return []
    out: list[Listing] = []
    seen: set[str] = set()
    for row in summaries:
        if not isinstance(row, dict):
            continue
        listing = parse_ebay_item(row, search_name)
        if listing is None:
            continue
        if listing.listing_id in seen:
            continue
        seen.add(listing.listing_id)
        out.append(listing)
    return out

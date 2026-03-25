from __future__ import annotations

import json
import math
import re
from json import JSONDecoder
from typing import Any

from bikefinder.models import Listing

_INITIAL_MARKER = "window.__INITIAL_STATE__="

_REL_NEXT_RE = re.compile(
    r'<link\s+rel="next"[^>]*href="([^"]+)"',
    re.IGNORECASE,
)


def extract_initial_state(html: str) -> dict[str, Any] | None:
    i = html.find(_INITIAL_MARKER)
    if i < 0:
        return None
    start = html.find("{", i)
    if start < 0:
        return None
    try:
        data, _ = JSONDecoder().raw_decode(html, start)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _listing_url(category_slug: str, title_slug: str, listing_id: int | str) -> str:
    slug = (category_slug or "").strip().strip("/")
    ts = (title_slug or "").strip().strip("/")
    return f"https://www.njuskalo.hr/{slug}/{ts}-oglas-{int(listing_id)}"


def _location_from_abstracts(abstracts: Any) -> str | None:
    if not isinstance(abstracts, list):
        return None
    for item in abstracts:
        if not isinstance(item, dict):
            continue
        cap = str(item.get("caption") or "").strip()
        if cap.lower() == "lokacija":
            val = str(item.get("value") or "").strip()
            return val or None
    return None


def _body_from_abstracts(abstracts: Any) -> str:
    if not isinstance(abstracts, list):
        return ""
    parts: list[str] = []
    for item in abstracts:
        if not isinstance(item, dict):
            continue
        cap = str(item.get("caption") or "").strip()
        val = str(item.get("value") or "").strip()
        if cap and val:
            parts.append(f"{cap}: {val}")
        elif val:
            parts.append(val)
    return "\n".join(parts).strip()


def _listing_from_entry(entry: dict[str, Any], search_name: str) -> Listing | None:
    raw_id = entry.get("id")
    if raw_id is None:
        return None
    try:
        lid = int(raw_id)
    except (TypeError, ValueError):
        return None
    cat = entry.get("categorySlug")
    tslug = entry.get("titleSlug")
    if not isinstance(cat, str) or not isinstance(tslug, str):
        return None
    url = _listing_url(cat, tslug, lid)
    title = str(entry.get("title") or "").strip() or str(lid)
    price = entry.get("priceFormatted")
    price_s = str(price).strip() if isinstance(price, str) else None
    posted = entry.get("createdAt")
    posted_s = str(posted).strip() if isinstance(posted, str) else None
    img = entry.get("image")
    images = [str(img).strip()] if isinstance(img, str) and img.strip().startswith("http") else []
    loc = _location_from_abstracts(entry.get("abstracts"))
    body = _body_from_abstracts(entry.get("abstracts"))
    return Listing(
        listing_id=str(lid),
        url=url,
        title=title,
        body=body,
        posted_at=posted_s,
        price=price_s,
        location=loc,
        image_urls=images,
        search_name=search_name,
    )


def _iter_page_list_entries(page_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Order: promoted, user-promoted, regular (dedupe by id, first wins)."""
    seen_set: set[int] = set()
    out: list[dict[str, Any]] = []

    def push(entries: Any) -> None:
        if not isinstance(entries, list):
            return
        for e in entries:
            if not isinstance(e, dict):
                continue
            try:
                i = int(e["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if i in seen_set:
                continue
            seen_set.add(i)
            out.append(e)

    push(page_data.get("promotedListings"))
    push(page_data.get("userPromotedListings"))
    push(page_data.get("regularListings"))
    return out


def listings_from_search_html(
    html: str,
    search_name: str,
) -> tuple[list[Listing], int, int]:
    """Parse Njuskalo category SERP from ``window.__INITIAL_STATE__`` JSON.

    Returns ``(listings, total_results, total_pages)``.
    """
    state = extract_initial_state(html)
    if not state:
        return [], 0, 1

    try:
        page_data = state["browseListingsStore"]["pageData"]
        if not isinstance(page_data, dict):
            return [], 0, 1
    except (KeyError, TypeError):
        return [], 0, 1

    entries = _iter_page_list_entries(page_data)
    listings: list[Listing] = []
    for e in entries:
        parsed = _listing_from_entry(e, search_name)
        if parsed:
            listings.append(parsed)

    total_results = int(page_data.get("listingsCount") or 0)
    total_pages = int(page_data.get("totalPageCount") or 0)

    if total_results > 0 and total_pages <= 0:
        reg = page_data.get("regularListings")
        per_page = max(1, len(reg) if isinstance(reg, list) else len(listings))
        total_pages = max(1, math.ceil(total_results / per_page))

    return listings, total_results, max(1, total_pages)


def serp_has_next_page(html: str) -> bool:
    return bool(_REL_NEXT_RE.search(html))

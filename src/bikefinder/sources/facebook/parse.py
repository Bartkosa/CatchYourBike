from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from bikefinder.models import Listing

_ITEM_HREF_RE = re.compile(
    r"https?://(?:www\.|m\.|web\.)?facebook\.com/marketplace/item/(\d+)/?",
    re.IGNORECASE,
)


def normalize_marketplace_item_url(href: str, listing_id: str) -> str:
    h = (href or "").strip()
    if h.startswith("/"):
        return f"https://www.facebook.com{h.split('?', 1)[0].rstrip('/')}/"
    m = _ITEM_HREF_RE.match(h)
    if m:
        return f"https://www.facebook.com/marketplace/item/{m.group(1)}/"
    return f"https://www.facebook.com/marketplace/item/{listing_id}/"


def listing_from_card(
    card: dict[str, Any],
    *,
    search_name: str,
    search_page_url: str,
    posted_at: str | None,
) -> Listing | None:
    lid = str(card.get("id") or "").strip()
    if not lid.isdigit():
        return None
    title = (card.get("title") or "").strip() or f"Marketplace item {lid}"
    price = (card.get("price") or "").strip() or None
    location = (card.get("location") or "").strip() or None
    href = (card.get("href") or "").strip()
    url = normalize_marketplace_item_url(href, lid)
    images: list[str] = []
    for u in card.get("image_urls") or []:
        s = str(u).strip()
        if s and s not in images:
            images.append(s)
    body = " ".join(x for x in (title, price or "", location or "") if x).strip()
    return Listing(
        listing_id=lid,
        url=url,
        title=title,
        body=body,
        posted_at=posted_at,
        price=price,
        location=location,
        image_urls=images,
        search_name=search_name,
        search_page_url=search_page_url,
    )


def listings_from_cards(
    cards: list[dict[str, Any]],
    *,
    search_name: str,
    search_page_url: str,
    posted_at_for_index: Callable[[int], str | None],
) -> list[Listing]:
    out: list[Listing] = []
    for idx, card in enumerate(cards):
        ts = card.get("creation_ts")
        posted: str | None = None
        if isinstance(ts, (int, float)) and ts > 1_000_000_000:
            try:
                posted = datetime.fromtimestamp(
                    float(ts), tz=timezone.utc
                ).replace(microsecond=0).isoformat()
            except (ValueError, OSError):
                posted = None
        if posted is None:
            posted = (card.get("posted_at") or "").strip() or None
        if posted is None:
            posted = posted_at_for_index(idx)
        L = listing_from_card(
            card,
            search_name=search_name,
            search_page_url=search_page_url,
            posted_at=posted,
        )
        if L is not None:
            out.append(L)
    return out

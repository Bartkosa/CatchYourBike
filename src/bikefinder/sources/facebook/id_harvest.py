from __future__ import annotations

import re
from typing import Any

# Listing ids in JSON/HTML: plain path, URL-encoded path, or over-escaped JSON strings.
_ITEM_ID_PATTERNS = (
    re.compile(r"marketplace/item/(\d{10,20})\b", re.IGNORECASE),
    re.compile(r"marketplace%2[Ff]item%2[Ff](\d{10,20})\b"),
    # JSON-escaped forward slashes: marketplace\/item\/1234567890123456
    re.compile(r"marketplace\\/item\\/(\d{10,20})\b"),
)


def harvest_listing_ids_from_text(body: str, *, max_ids: int = 500) -> list[str]:
    """Extract unique listing ids from a GraphQL/HTML/JSON string."""
    if not body:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for pat in _ITEM_ID_PATTERNS:
        for m in pat.finditer(body):
            lid = m.group(1)
            if lid in seen:
                continue
            seen.add(lid)
            out.append(lid)
            if len(out) >= max_ids:
                return out
    return out


_TYPENAME_MARKERS = (
    "MarketplaceListing",
    "StoriesMarketplaceListing",
    "GroupCommerceProductItem",
    "MarketplaceC2CListing",
    "CommerceProductItem",
    "MarketplaceFeedItem",
)

_LISTING_HINT_KEYS = frozenset(
    {
        "listing_price",
        "formatted_price",
        "strikethrough_price",
        "listing_pricing",
        "price_breakdown_summary",
        "marketplace_listing_title",
        "redacted_description",
        "custom_title",
        "is_on_marketplace",
    }
)


def _listing_id_string(v: object) -> str | None:
    if isinstance(v, str) and v.isdigit() and 10 <= len(v) <= 20:
        return v
    return None


def _dict_suggests_marketplace_listing(d: dict[str, Any]) -> bool:
    tn = d.get("__typename")
    if isinstance(tn, str):
        for m in _TYPENAME_MARKERS:
            if m in tn:
                return True
    if any(k in d for k in _LISTING_HINT_KEYS):
        return True
    return False


def harvest_listing_ids_from_json(obj: Any, *, max_ids: int = 500) -> list[str]:
    """Walk decoded GraphQL/JSON for Marketplace listing-shaped objects and collect ``id``."""
    seen: set[str] = set()
    out: list[str] = []

    def add(lid: str) -> None:
        if len(out) >= max_ids:
            return
        if lid not in seen:
            seen.add(lid)
            out.append(lid)

    def visit(x: Any) -> None:
        if len(out) >= max_ids:
            return
        if isinstance(x, dict):
            if _dict_suggests_marketplace_listing(x):
                for key in ("id", "listing_id"):
                    sid = _listing_id_string(x.get(key))
                    if sid:
                        add(sid)
            for key in ("marketplace_listing_id", "product_item_id"):
                sid = _listing_id_string(x.get(key))
                if sid:
                    add(sid)
            for v in x.values():
                visit(v)
        elif isinstance(x, list):
            for it in x:
                visit(it)

    visit(obj)
    return out

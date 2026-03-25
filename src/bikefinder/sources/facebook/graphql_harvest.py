from __future__ import annotations

from typing import Any


def _listing_photo_uris_from_node(d: dict[str, Any]) -> list[str]:
    """Pull fbcdn/scontent image URLs from common Marketplace listing photo shapes."""
    out: list[str] = []

    def take_url(u: object) -> None:
        if not isinstance(u, str) or len(out) >= 8:
            return
        if "fbcdn.net" not in u and "scontent." not in u:
            return
        if u not in out:
            out.append(u)

    for key in (
        "primary_listing_photo",
        "listing_photos",
        "primary_photo",
        "photo",
        "image",
        "thumbnail_image",
        "cover_photo",
    ):
        v = d.get(key)
        if isinstance(v, dict):
            take_url(v.get("uri"))
            take_url(v.get("url"))
            for nk in ("image", "photo", "full_image"):
                sub = v.get(nk)
                if isinstance(sub, dict):
                    take_url(sub.get("uri"))
                    take_url(sub.get("url"))
        elif isinstance(v, list):
            for item in v[:12]:
                if isinstance(item, dict):
                    take_url(item.get("uri"))
                    take_url(item.get("url"))
    return out


def _maybe_float_ts(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1_000_000_000:
            return x
        return None
    return None


def harvest_marketplace_hints(obj: Any, out: dict[str, dict[str, Any]]) -> None:
    """Best-effort walk of GraphQL JSON for listing id + creation time / title."""
    if isinstance(obj, dict):
        tid = obj.get("id")
        if isinstance(tid, str) and tid.isdigit() and len(tid) >= 8:
            bucket = out.setdefault(tid, {})
            for key in (
                "creation_time",
                "creationTime",
                "story_creation_time",
                "created_time",
                "createdTime",
            ):
                if key in obj:
                    ts = _maybe_float_ts(obj.get(key))
                    if ts is not None:
                        bucket["creation_ts"] = ts
                        break
            for key in (
                "title",
                "primary_listing_title",
                "marketplace_listing_title",
                "listing_title",
            ):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    bucket["title"] = val.strip()
                    break
            price_obj = obj.get("listing_price") or obj.get("formatted_price")
            if isinstance(price_obj, dict):
                fv = price_obj.get("formatted_amount") or price_obj.get("text")
                if isinstance(fv, str) and fv.strip():
                    bucket["price"] = fv.strip()
            elif isinstance(price_obj, str) and price_obj.strip():
                bucket["price"] = price_obj.strip()
            for u in _listing_photo_uris_from_node(obj):
                lst = bucket.setdefault("image_urls", [])
                if u not in lst:
                    lst.append(u)
        for v in obj.values():
            harvest_marketplace_hints(v, out)
    elif isinstance(obj, list):
        for x in obj:
            harvest_marketplace_hints(x, out)

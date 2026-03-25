from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from bikefinder.models import Listing

# Subito-style UTC string so Postgres TEXT comparisons with min_listing_date work.
_POSTED_AT_DB_FMT = "%Y-%m-%d %H:%M:%S"


def _normalize_posted_at_for_db(value: Any) -> str | None:
    """Wallapop often sends ``created_at`` as unix milliseconds (string or int); normalize for storage."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            raw = float(value)
            if raw > 10_000_000_000:
                raw = raw / 1000.0
            dt = datetime.fromtimestamp(raw, tz=timezone.utc)
            return dt.strftime(_POSTED_AT_DB_FMT)
        except (ValueError, OSError):
            return None
    text = _as_str(value)
    if not text:
        return None
    if text.isdigit():
        try:
            raw = int(text)
            if raw > 10_000_000_000:
                raw = raw / 1000.0
            dt = datetime.fromtimestamp(raw, tz=timezone.utc)
            return dt.strftime(_POSTED_AT_DB_FMT)
        except (ValueError, OSError):
            return text
    if text.endswith("Z") or "T" in text:
        try:
            normalized = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime(_POSTED_AT_DB_FMT)
        except ValueError:
            pass
    return text


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _pick(obj: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in obj and obj[key] is not None:
            return obj[key]
    return None


def _pick_path(obj: dict[str, Any], paths: list[tuple[str, ...]]) -> Any:
    for path in paths:
        cur: Any = obj
        ok = True
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                ok = False
                break
            cur = cur[key]
        if ok and cur is not None:
            return cur
    return None


def _wallapop_url_from_image_dict(image: dict[str, Any]) -> str | None:
    """Wallapop search payloads use ``{\"urls\": {\"big\": \"https://...\", ...}}``."""
    urls_obj = image.get("urls")
    if isinstance(urls_obj, dict):
        for key in ("big", "medium", "small", "original", "default"):
            sval = _as_str(urls_obj.get(key))
            if sval:
                return sval
    if isinstance(urls_obj, list):
        for entry in urls_obj:
            if isinstance(entry, str):
                sval = _as_str(entry)
                if sval:
                    return sval
            if isinstance(entry, dict) and isinstance(entry.get("urls"), dict):
                for key in ("big", "medium", "small"):
                    sval = _as_str(entry["urls"].get(key))
                    if sval:
                        return sval
    return _as_str(_pick(image, "url", "big", "large", "small", "original"))


def _image_urls(raw_item: dict[str, Any]) -> list[str]:
    images = _pick(raw_item, "images", "pictures", "photos")
    if not isinstance(images, list):
        return []
    out: list[str] = []
    for image in images:
        if isinstance(image, str):
            val = _as_str(image)
            if val:
                out.append(val)
            continue
        if not isinstance(image, dict):
            continue
        sval = _wallapop_url_from_image_dict(image)
        if sval:
            out.append(sval)
    return out


def _build_item_url(raw_item: dict[str, Any]) -> str | None:
    direct = _as_str(_pick(raw_item, "url", "web_url", "share_url", "item_url"))
    if direct:
        if direct.startswith("http://") or direct.startswith("https://"):
            return direct
        if direct.startswith("/"):
            return f"https://it.wallapop.com{direct}"
        return f"https://it.wallapop.com/{direct}"

    slug = _as_str(_pick(raw_item, "web_slug", "slug"))
    if slug:
        return f"https://it.wallapop.com/item/{slug}"

    item_id = _as_str(_pick(raw_item, "id", "item_id"))
    if item_id:
        return f"https://it.wallapop.com/item/{quote(item_id, safe='')}"
    return None


def parse_wallapop_item(raw_item: dict[str, Any], search_name: str) -> Listing | None:
    item_id = _as_str(_pick(raw_item, "id", "item_id"))
    if not item_id:
        return None
    url = _build_item_url(raw_item)
    if not url:
        return None

    title = _as_str(_pick(raw_item, "title", "subject", "name")) or ""
    body = _as_str(_pick(raw_item, "description", "body")) or ""
    posted_at = _normalize_posted_at_for_db(
        _pick(
            raw_item,
            "published_date",
            "creation_date",
            "created_at",
            "created_date",
            "modified_at",
            "modified_date",
            "updated_date",
        )
    )

    price_val = _pick_path(
        raw_item,
        [
            ("sale_price", "amount"),
            ("price", "amount"),
            ("price",),
            ("sale_price",),
        ],
    )
    price = _as_str(price_val)

    city = _as_str(_pick_path(raw_item, [("location", "city"), ("user", "location", "city")]))
    region = _as_str(_pick_path(raw_item, [("location", "region"), ("user", "location", "region")]))
    location = ", ".join([p for p in [city, region] if p]) or None

    return Listing(
        listing_id=item_id,
        url=url,
        title=title,
        body=body,
        posted_at=posted_at,
        price=price,
        location=location,
        image_urls=_image_urls(raw_item),
        search_name=search_name,
    )


def _extract_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    nested_section_items = _pick_path(
        payload,
        [
            ("data", "section", "items"),
            ("data", "items"),
            ("section", "items"),
        ],
    )
    if isinstance(nested_section_items, list):
        return [x for x in nested_section_items if isinstance(x, dict)]

    candidates = [
        payload.get("search_objects"),
        payload.get("items"),
        payload.get("results"),
        payload.get("data"),
    ]
    for cand in candidates:
        if isinstance(cand, list):
            return [x for x in cand if isinstance(x, dict)]
        if isinstance(cand, dict):
            for key in ("search_objects", "items", "results"):
                nested = cand.get(key)
                if isinstance(nested, list):
                    return [x for x in nested if isinstance(x, dict)]
    return []


def _extract_next_cursor(payload: dict[str, Any]) -> str | None:
    """Wallapop section responses put the continuation token in ``meta.next_page`` (JWT)."""
    raw = _pick_path(payload, [("meta", "next_page")])
    if raw is None:
        raw = _pick(
            payload,
            "next_page",
            "next_page_id",
            "next",
            "cursor",
            "search_id",
            "last_id",
        )
    if raw is None:
        raw = _pick_path(
            payload,
            [
                ("meta", "search_id"),
                ("data", "tracking", "search_id"),
                ("data", "section", "search_id"),
            ],
        )
    text = _as_str(raw)
    return text


def listings_from_wallapop_payload(
    payload: dict[str, Any], search_name: str
) -> tuple[list[Listing], str | None]:
    out: list[Listing] = []
    seen_ids: set[str] = set()
    for item in _extract_items(payload):
        listing = parse_wallapop_item(item, search_name)
        if not listing:
            continue
        if listing.listing_id in seen_ids:
            continue
        seen_ids.add(listing.listing_id)
        out.append(listing)
    return out, _extract_next_cursor(payload)

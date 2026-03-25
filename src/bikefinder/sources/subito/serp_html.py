from __future__ import annotations

import json
import re
from typing import Any

from bikefinder.models import Listing

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def has_next_data_script(html: str) -> bool:
    """True if HTML embeds Subito/Next.js ``__NEXT_DATA__`` (curl SSR vs bot wall / shell)."""
    return bool(_NEXT_DATA_RE.search(html))


def extract_next_data_json(html: str) -> dict[str, Any]:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("No __NEXT_DATA__ script tag found in HTML")
    return json.loads(m.group(1))


def _price_from_features(features: dict[str, Any] | None) -> str | None:
    if not features:
        return None
    price = features.get("/price")
    if not price or not isinstance(price, dict):
        return None
    vals = price.get("values") or []
    if not vals:
        return None
    v = vals[0]
    if isinstance(v, dict):
        return str(v.get("value") or v.get("key") or "")
    return str(v)


def _location_from_geo(geo: dict[str, Any] | None) -> str | None:
    if not geo or not isinstance(geo, dict):
        return None
    parts: list[str] = []
    town = (geo.get("town") or {}) if isinstance(geo.get("town"), dict) else {}
    city = (geo.get("city") or {}) if isinstance(geo.get("city"), dict) else {}
    region = (geo.get("region") or {}) if isinstance(geo.get("region"), dict) else {}
    if town.get("value"):
        parts.append(str(town["value"]))
    if city.get("value"):
        parts.append(str(city["value"]))
    if region.get("value"):
        parts.append(str(region["value"]))
    return ", ".join(parts) if parts else None


def _listing_id_from_urn(urn: str) -> str:
    # id:ad:uuid:list:610736596
    if ":list:" in urn:
        return urn.split(":list:")[-1]
    return urn


def _image_urls(images: list[Any] | None) -> list[str]:
    if not images:
        return []
    out: list[str] = []
    for im in images:
        if not isinstance(im, dict):
            continue
        base = im.get("cdnBaseUrl")
        if base:
            sep = "&" if "?" in base else "?"
            out.append(f"{base}{sep}rule=gallery-desktop-2x-auto")
    return out


def parse_ad_item(raw: dict[str, Any], search_name: str) -> Listing | None:
    if raw.get("kind") != "AdItem":
        return None
    urn = raw.get("urn") or ""
    listing_id = _listing_id_from_urn(str(urn))
    urls = raw.get("urls") or {}
    url = urls.get("default") or urls.get("mobile") or ""
    if not url or not listing_id:
        return None
    features = raw.get("features") if isinstance(raw.get("features"), dict) else None
    return Listing(
        listing_id=listing_id,
        url=url,
        title=str(raw.get("subject") or "").strip(),
        body=str(raw.get("body") or "").strip(),
        posted_at=str(raw.get("date") or "").strip() or None,
        price=_price_from_features(features),
        location=_location_from_geo(raw.get("geo") if isinstance(raw.get("geo"), dict) else None),
        image_urls=_image_urls(raw.get("images") if isinstance(raw.get("images"), list) else None),
        search_name=search_name,
    )


def _item_dict_from_list_entry(entry: Any) -> dict[str, Any] | None:
    """Normalize list / galleryList / boostedItems row to an ad item dict."""
    if not isinstance(entry, dict):
        return None
    inner = entry.get("item")
    if isinstance(inner, dict):
        return inner
    # galleryList rows are often the AdItem itself (no nested "item")
    return entry


def listings_from_search_html(html: str, search_name: str) -> tuple[list[Listing], int, int]:
    """Returns (listings, total_results, total_pages).

    Subito puts ~30 main rows in ``items.list``. Promoted rows in
    ``items.galleryList`` (and ``items.boostedItems``) are merged in as well,
    with de-duplication by listing id.
    """
    data = extract_next_data_json(html)
    initial = (
        data.get("props", {})
        .get("pageProps", {})
        .get("initialState", {})
    )
    items_block = initial.get("items") or {}
    total = int(items_block.get("total") or 0)
    total_pages = int(items_block.get("totalPages") or 1)
    raw_list = items_block.get("list") or []

    out: list[Listing] = []
    seen_ids: set[str] = set()

    def _append_from_entries(entries: list[Any]) -> None:
        for entry in entries:
            item = _item_dict_from_list_entry(entry)
            if not item:
                continue
            parsed = parse_ad_item(item, search_name)
            if not parsed:
                continue
            if parsed.listing_id in seen_ids:
                continue
            seen_ids.add(parsed.listing_id)
            out.append(parsed)

    if isinstance(raw_list, list):
        _append_from_entries(raw_list)

    gallery = items_block.get("galleryList") or []
    if isinstance(gallery, list):
        _append_from_entries(gallery)

    boosted = items_block.get("boostedItems") or []
    if isinstance(boosted, list):
        _append_from_entries(boosted)

    return out, total, total_pages


def append_query(url: str, **params: str | int) -> str:
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    u = urlparse(url)
    q = dict(parse_qsl(u.query, keep_blank_values=True))
    for k, v in params.items():
        q[k] = str(v)
    new_query = urlencode(q)
    return urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))

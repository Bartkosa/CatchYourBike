from __future__ import annotations

import math
import re

from bikefinder.models import Listing

_BASE = "https://www.kupujemprodajem.com"

# Next.js CSS modules: stable prefix before ``__``.
_ARTICLE_RE = re.compile(
    r'<article\s+class="[^"]*AdItem_adHolder__[^"]*"[^>]*>(.*?)</article>',
    re.IGNORECASE | re.DOTALL,
)
_OGLAS_ID_RE = re.compile(r"/oglas/(\d+)")
_TITLE_RE = re.compile(
    r'<div\s+class="[^"]*AdItem_name__[^"]*"[^>]*>([^<]+)</div>',
    re.IGNORECASE,
)
_IMG_RE = re.compile(
    r'<img[^>]+src="(https://images\.kupujemprodajem\.com[^"]+)"',
    re.IGNORECASE,
)
_DESC_RE = re.compile(
    r'<p\s+class="">([^<]*)</p>',
    re.IGNORECASE,
)
_PRICE_RE = re.compile(
    r'<div\s+class="[^"]*AdItem_price__[^"]*"[^>]*>\s*<div>\s*([^<]+?)\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
_LOC_RE = re.compile(
    r'<div\s+class="[^"]*AdItem_originAndPromoLocation__[^"]*"[^>]*>\s*<p>([^<]+)',
    re.IGNORECASE | re.DOTALL,
)
_POSTED_RE = re.compile(
    r'<div\s+class="[^"]*AdItem_postedStatus__[^"]*"[^>]*>\s*<p>[\s\S]*?</svg>([^<]+)</p>',
    re.IGNORECASE,
)
_TITLE_PAGE_RE = re.compile(
    r"<title[^>]*>\s*Strana\s+(\d+)\s+od\s+(\d+)",
    re.IGNORECASE,
)
_META_COUNT_RE = re.compile(
    r'<meta\s+name="description"\s+content="(\d+)\s+oglasa',
    re.IGNORECASE,
)


def _first_href_oglas(block: str) -> str | None:
    for m in re.finditer(r'href="(/[^"]+/oglas/\d+)"', block, re.IGNORECASE):
        return m.group(1)
    return None


def _listing_from_article(block: str, search_name: str) -> Listing | None:
    href = _first_href_oglas(block)
    if not href:
        return None
    mid = _OGLAS_ID_RE.search(href)
    if not mid:
        return None
    lid = mid.group(1)
    url = f"{_BASE}{href}" if href.startswith("/") else href

    tm = _TITLE_RE.search(block)
    title = (tm.group(1).strip() if tm else "") or lid

    imgs = [m.group(1).strip() for m in _IMG_RE.finditer(block)]
    images = [u for u in imgs if u.startswith("http")]

    dm = _DESC_RE.search(block)
    body = (dm.group(1).strip() if dm else "").replace("\xa0", " ")

    pm = _PRICE_RE.search(block)
    price = pm.group(1).strip().replace("\xa0", " ") if pm else None

    lm = _LOC_RE.search(block)
    location = lm.group(1).strip().replace("\xa0", " ") if lm else None

    posted_m = _POSTED_RE.search(block)
    posted_raw = posted_m.group(1).strip().replace("\xa0", " ") if posted_m else None

    return Listing(
        listing_id=lid,
        url=url,
        title=title,
        body=body,
        posted_at=posted_raw,
        price=price,
        location=location,
        image_urls=images[:1],
        search_name=search_name,
    )


def listings_from_search_html(
    html: str,
    search_name: str,
) -> tuple[list[Listing], int, int]:
    """Parse KP category SERP HTML (Next.js rendered ``AdItem_*`` cards).

    Returns ``(listings, total_results, total_pages)``.
    """
    listings: list[Listing] = []
    seen: set[str] = set()
    for m in _ARTICLE_RE.finditer(html):
        parsed = _listing_from_article(m.group(1), search_name)
        if not parsed or parsed.listing_id in seen:
            continue
        seen.add(parsed.listing_id)
        listings.append(parsed)

    tm = _TITLE_PAGE_RE.search(html)
    total_pages = int(tm.group(2)) if tm else 1

    cm = _META_COUNT_RE.search(html)
    total_results = int(cm.group(1)) if cm else len(listings)

    if total_pages <= 0:
        total_pages = 1
    if total_results > 0 and len(listings) > 0 and total_pages == 1 and len(listings) < total_results:
        total_pages = max(1, math.ceil(total_results / len(listings)))

    return listings, total_results, total_pages


def serp_has_next_page(html: str) -> bool:
    m = _TITLE_PAGE_RE.search(html)
    if m:
        return int(m.group(1)) < int(m.group(2))
    return False

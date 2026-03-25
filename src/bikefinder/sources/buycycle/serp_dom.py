from __future__ import annotations

from collections.abc import Callable

from bikefinder.models import Listing

_CARD_JS = r"""
(elements) => elements.map((el) => {
  const id = el.getAttribute("data-cnstrc-item-id");
  const name = el.getAttribute("data-cnstrc-item-name") || "";
  const price = el.getAttribute("data-cnstrc-item-price") || "";
  const a = el.querySelector("a[href]");
  const href = a ? a.getAttribute("href") : "";
  const im = el.querySelector("img");
  const src = im ? (im.currentSrc || im.getAttribute("src") || "") : "";
  const text = (el.innerText || "").trim();
  return { id, name, price, href, src, text };
})
"""


def card_snapshot_js() -> str:
    return _CARD_JS.strip()


def listing_from_snapshot(
    row: dict[str, str],
    *,
    search_name: str,
    search_page_url: str,
    posted_at: str | None,
) -> Listing | None:
    """Turn Constructor.io card attributes + link into a ``Listing``."""
    lid = (row.get("id") or "").strip()
    if not lid:
        return None
    href = (row.get("href") or "").strip()
    if not href:
        return None
    if href.startswith("/"):
        url = "https://buycycle.com" + href
    elif href.startswith("http"):
        url = href
    else:
        return None

    title = (row.get("name") or "").strip() or lid
    body = (row.get("text") or "").strip()
    if len(body) > 1200:
        body = body[:1197] + "..."

    raw_price = (row.get("price") or "").strip()
    price = f"€ {raw_price}" if raw_price else None

    img = (row.get("src") or "").strip()
    if img.startswith("//"):
        img = "https:" + img
    image_urls = [img] if img.startswith("http") else []

    return Listing(
        listing_id=lid,
        url=url,
        title=title,
        body=body,
        posted_at=posted_at,
        price=price,
        location=None,
        image_urls=image_urls,
        search_name=search_name,
        search_page_url=search_page_url,
    )


def listings_from_snapshots(
    rows: list[dict[str, str]],
    *,
    search_name: str,
    search_page_url: str,
    posted_at_for_index: Callable[[int], str | None],
) -> list[Listing]:
    out: list[Listing] = []
    for idx, row in enumerate(rows):
        posted = posted_at_for_index(idx)
        L = listing_from_snapshot(
            row,
            search_name=search_name,
            search_page_url=search_page_url,
            posted_at=posted,
        )
        if L:
            out.append(L)
    return out

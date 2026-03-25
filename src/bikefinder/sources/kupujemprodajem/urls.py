from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_KP_HOST = re.compile(r"(^|\.)kupujemprodajem\.com$", re.IGNORECASE)


def assert_kupujemprodajem_search_url(url: str) -> None:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not _KP_HOST.search(u.netloc or ""):
        raise ValueError(
            f"KupujemProdajem search url must be https://www.kupujemprodajem.com/... got {url!r}"
        )


def build_serp_url(
    search_url: str,
    *,
    price_min_eur: int,
    price_max_eur: int,
    page_1based: int,
) -> str:
    """Merge query: keep path and existing params; set ``page``, ``priceFrom``, ``priceTo``, ``hasPrice``."""
    u = urlparse(search_url.strip())
    pairs = parse_qsl(u.query, keep_blank_values=True)
    q: dict[str, str] = {}
    for k, v in pairs:
        q[k] = v
    q["page"] = str(max(1, int(page_1based)))
    q["priceFrom"] = str(int(price_min_eur))
    q["priceTo"] = str(int(price_max_eur))
    q["hasPrice"] = "yes"
    new_query = urlencode(list(q.items()))
    return urlunparse((u.scheme, u.netloc, u.path, "", new_query, ""))

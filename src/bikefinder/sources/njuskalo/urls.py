from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_NJUSKALO_HOST = re.compile(r"(^|\.)njuskalo\.hr$", re.IGNORECASE)


def assert_njuskalo_search_url(url: str) -> None:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not _NJUSKALO_HOST.search(u.netloc or ""):
        raise ValueError(
            f"Njuskalo search url must be https://www.njuskalo.hr/... got {url!r}"
        )


def build_serp_url(
    search_url: str,
    *,
    price_min_eur: int,
    price_max_eur: int,
    page_1based: int,
) -> str:
    """Merge query: keep existing params; set ``price[min]``, ``price[max]``, ``page``."""
    u = urlparse(search_url.strip())
    pairs = parse_qsl(u.query, keep_blank_values=True)
    q: dict[str, str] = {}
    for k, v in pairs:
        q[k] = v
    q["price[min]"] = str(int(price_min_eur))
    q["price[max]"] = str(int(price_max_eur))
    q["page"] = str(max(1, int(page_1based)))
    new_query = urlencode(list(q.items()))
    return urlunparse((u.scheme, u.netloc, u.path, "", new_query, ""))

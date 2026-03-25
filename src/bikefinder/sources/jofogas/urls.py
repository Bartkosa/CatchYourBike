from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_JOFOGAS_HOST = re.compile(r"(^|\.)jofogas\.hu$", re.IGNORECASE)


def assert_jofogas_search_url(url: str) -> None:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not _JOFOGAS_HOST.search(u.netloc or ""):
        raise ValueError(
            f"Jófogás search url must be https://www.jofogas.hu/... got {url!r}"
        )


def build_serp_url(
    search_url: str,
    *,
    price_min_huf: int,
    price_max_huf: int,
    page_1based: int,
) -> str:
    """Merge query: keep existing params; set ``min_price``, ``max_price`` (HUF), ``o`` (page)."""
    u = urlparse(search_url.strip())
    pairs = parse_qsl(u.query, keep_blank_values=True)
    q: dict[str, str] = {}
    for k, v in pairs:
        q[k] = v
    q["min_price"] = str(max(0, int(price_min_huf)))
    q["max_price"] = str(max(0, int(price_max_huf)))
    q["o"] = str(max(1, int(page_1based)))
    new_query = urlencode(list(q.items()))
    return urlunparse((u.scheme, u.netloc, u.path, "", new_query, ""))

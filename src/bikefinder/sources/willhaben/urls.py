from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_WH_HOST = re.compile(r"(^|\.)willhaben\.at$", re.IGNORECASE)
_MARKTPL_PATH = re.compile(
    r"^/iad/kaufen-und-verkaufen/marktplatz/",
    re.IGNORECASE,
)


def assert_willhaben_search_url(url: str) -> None:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not _WH_HOST.search(u.netloc or ""):
        raise ValueError(
            f"willhaben search url must be https://www.willhaben.at/... got {url!r}"
        )
    path = u.path or ""
    if not _MARKTPL_PATH.search(path):
        raise ValueError(
            f"willhaben path must be under /iad/kaufen-und-verkaufen/marktplatz/, got {path!r}"
        )


def build_serp_url(
    search_url: str,
    *,
    price_min_eur: int,
    price_max_eur: int,
    page_1based: int,
) -> str:
    """Merge query: keep existing params; set PRICE_FROM, PRICE_TO, sort=1, page."""
    u = urlparse(search_url.strip())
    pairs = parse_qsl(u.query, keep_blank_values=True)
    q: dict[str, str] = {}
    for k, v in pairs:
        q[k] = v
    q["PRICE_FROM"] = str(int(price_min_eur))
    q["PRICE_TO"] = str(int(price_max_eur))
    q["sort"] = "1"
    q["page"] = str(max(1, int(page_1based)))
    new_query = urlencode(sorted(q.items()))
    return urlunparse((u.scheme, u.netloc, u.path, "", new_query, ""))

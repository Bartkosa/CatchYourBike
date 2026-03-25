from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_BC_HOST = re.compile(r"(^|\.)buycycle\.com$", re.IGNORECASE)


def assert_buycycle_search_url(url: str) -> None:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not _BC_HOST.search(u.netloc or ""):
        raise ValueError(
            f"buycycle search url must be https://buycycle.com/... got {url!r}"
        )
    path = (u.path or "").lower()
    if "/shop/" not in path:
        raise ValueError(
            f"buycycle path must include /shop/, got path {u.path!r}"
        )


def _path_segments(path: str) -> list[str]:
    return [s for s in path.strip("/").split("/") if s]


def _strip_price_segments(segments: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(segments):
        if segments[i] == "min-price" and i + 1 < len(segments):
            i += 2
            continue
        if segments[i] == "max-price" and i + 1 < len(segments):
            i += 2
            continue
        out.append(segments[i])
        i += 1
    return out


def _strip_page_segments(segments: list[str]) -> list[str]:
    """Remove trailing ``.../page/<n>`` (buycycle paginates in the path)."""
    segs = list(segments)
    while len(segs) >= 2 and segs[-2] == "page" and segs[-1].isdigit():
        segs = segs[:-2]
    return segs


def _inject_price_segments(
    segments: list[str], *, price_min_eur: int, price_max_eur: int
) -> list[str]:
    segs = _strip_price_segments(_strip_page_segments(segments))
    block = ["min-price", str(int(price_min_eur)), "max-price", str(int(price_max_eur))]
    if "sort-by" in segs:
        j = segs.index("sort-by")
        return segs[:j] + block + segs[j:]
    return segs + block


def build_serp_url(
    search_url: str,
    *,
    price_min_eur: int,
    price_max_eur: int,
    page_1based: int,
) -> str:
    """Merge path price filters and ``/page/<n>`` suffix (page >= 2)."""
    u = urlparse(search_url.strip())
    segments = _path_segments(u.path or "")
    new_segments = _inject_price_segments(
        segments, price_min_eur=price_min_eur, price_max_eur=price_max_eur
    )
    p = max(1, int(page_1based))
    if p >= 2:
        new_segments = new_segments + ["page", str(p)]
    new_path = "/" + "/".join(new_segments)
    pairs = parse_qsl(u.query, keep_blank_values=True)
    q: dict[str, str] = {k: v for k, v in dict(pairs).items() if k != "page"}
    new_query = urlencode(sorted(q.items())) if q else ""
    return urlunparse((u.scheme, u.netloc, new_path, "", new_query, ""))

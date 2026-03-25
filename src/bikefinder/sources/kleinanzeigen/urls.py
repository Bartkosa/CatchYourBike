from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

_KA_HOST = re.compile(r"(^|\.)kleinanzeigen\.de$", re.IGNORECASE)
# Kleinanzeigen category segment is usually like: `c217`
# but it may include additional constraints joined with `+`, e.g.:
# `c217+fahrraeder.type_s:rennrad`.
_CAT_SEG = re.compile(r"^c\d+(?:\+.+)?$")


def assert_kleinanzeigen_search_url(url: str) -> None:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not _KA_HOST.search(u.netloc or ""):
        raise ValueError(
            f"Kleinanzeigen search url must be https://www.kleinanzeigen.de/... got {url!r}"
        )
    segs = [s for s in (u.path or "").strip("/").split("/") if s]
    if len(segs) < 2 or not segs[0].startswith("s-") or not _CAT_SEG.match(segs[-1]):
        raise ValueError(
            "Kleinanzeigen path must look like /s-<slug>/c<id> (category may include '+...'), "
            f"(optional preis / seite), got path {u.path!r}"
        )


def _slug_and_category(path: str) -> tuple[str, str]:
    segs = [s for s in path.strip("/").split("/") if s]
    if len(segs) < 2:
        raise ValueError(f"Kleinanzeigen path too short: {path!r}")
    if not segs[0].startswith("s-"):
        raise ValueError(f"Kleinanzeigen path must start with /s-..., got {path!r}")
    if not _CAT_SEG.match(segs[-1]):
        raise ValueError(f"Kleinanzeigen path must end with /c<id> (optionally '+...'), got {path!r}")
    slug = segs[0]
    category = segs[-1]
    return slug, category


def build_serp_url(
    search_url: str,
    *,
    price_min_eur: int,
    price_max_eur: int,
    page_1based: int,
) -> str:
    """Insert ``preis:min:max`` and optional ``seite:n`` (n>=2) before ``/c…``."""
    u = urlparse(search_url.strip())
    slug, category = _slug_and_category(u.path or "")
    parts = [slug, f"preis:{int(price_min_eur)}:{int(price_max_eur)}"]
    if page_1based >= 2:
        parts.append(f"seite:{int(page_1based)}")
    parts.append(category)
    new_path = "/" + "/".join(parts)
    return urlunparse((u.scheme, u.netloc, new_path, "", "", ""))

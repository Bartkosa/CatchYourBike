from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_ALLOWED_HOSTS = frozenset(
    {"www.facebook.com", "facebook.com", "m.facebook.com", "web.facebook.com"}
)
_MARKETPLACE_PATH_MARKER = "/marketplace/"


def assert_facebook_marketplace_url(search_url: str) -> None:
    raw = (search_url or "").strip()
    if not raw:
        raise ValueError("Facebook search url is empty")
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if ":" in host:
        host = host.split(":", 1)[0]
    if host not in _ALLOWED_HOSTS and not host.endswith(".facebook.com"):
        raise ValueError(
            f"Facebook source expects a facebook.com marketplace URL, got host {parsed.netloc!r}"
        )
    if _MARKETPLACE_PATH_MARKER not in (parsed.path or ""):
        raise ValueError(
            "Facebook source url must include a /marketplace/ path (paste the URL from Marketplace search)."
        )


def build_fetch_url(
    search_url: str,
    *,
    price_min_eur: int,
    price_max_eur: int,
    sort_by_default: str = "creation_time_descend",
) -> str:
    """Merge price band and default sort into the search URL query string.

    For ``/marketplace/np/<id>/search`` (location hub), returns the URL **unchanged** after strip.
    Re-parsing and re-encoding the query (or injecting ``minPrice``) has been observed to cause
    ``ERR_TOO_MANY_REDIRECTS`` with Meta. Put ``minPrice`` / ``maxPrice`` in the copied URL yourself.
    """
    assert_facebook_marketplace_url(search_url)
    raw = search_url.strip()
    u = urlparse(raw)
    path_lc = (u.path or "").lower()
    if "/marketplace/np/" in path_lc:
        return raw

    q = dict(parse_qsl(u.query, keep_blank_values=True))
    q["minPrice"] = str(int(price_min_eur))
    q["maxPrice"] = str(int(price_max_eur))
    sort_existing = (q.get("sortBy") or "").strip()
    if not sort_existing:
        q["sortBy"] = sort_by_default
    if "exact" not in q:
        q["exact"] = "false"
    return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(q), u.fragment))


def simplify_np_marketplace_url(url: str) -> str:
    """Drop partner / spotlight query keys on ``/marketplace/np/.../search`` URLs.

    Meta often returns ``ERR_TOO_MANY_REDIRECTS`` when ``partner_selected``,
    ``partner_ids[]``, or ``hide_organic_listings`` do not match the current session.
    """
    raw = (url or "").strip()
    u = urlparse(raw)
    if "/marketplace/np/" not in (u.path or "").lower():
        return raw
    pairs = parse_qsl(u.query, keep_blank_values=True)
    drop_keys = frozenset({"partner_selected", "hide_organic_listings"})
    out: list[tuple[str, str]] = []
    for k, v in pairs:
        kl = k.lower()
        if kl in drop_keys:
            continue
        if kl.startswith("partner_ids"):
            continue
        out.append((k, v))
    new_query = urlencode(out)
    return urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))

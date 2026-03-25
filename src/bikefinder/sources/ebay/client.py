from __future__ import annotations

import base64
import time
from typing import Any
from urllib.parse import urlencode

import httpx

EBAY_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
DEFAULT_OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"

_token: str | None = None
_token_expires_at: float = 0.0


def reset_oauth_token_cache() -> None:
    """Clear cached application token (for tests)."""
    global _token, _token_expires_at
    _token = None
    _token_expires_at = 0.0


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def get_application_access_token(
    client_id: str,
    client_secret: str,
    *,
    scope: str = DEFAULT_OAUTH_SCOPE,
    timeout: float = 30.0,
) -> str:
    global _token, _token_expires_at
    now = time.time()
    if _token and now < _token_expires_at - 60:
        return _token

    body = urlencode(
        {"grant_type": "client_credentials", "scope": scope},
        doseq=True,
    )
    r = httpx.post(
        EBAY_OAUTH_URL,
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": _basic_auth_header(client_id, client_secret),
        },
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    access = data.get("access_token")
    if not isinstance(access, str) or not access:
        raise RuntimeError("eBay OAuth response missing access_token")
    expires_in = data.get("expires_in")
    try:
        ttl = float(expires_in) if expires_in is not None else 7200.0
    except (TypeError, ValueError):
        ttl = 7200.0
    _token = access
    _token_expires_at = now + ttl
    return access


def build_search_filter_parts(
    *,
    price_min: float,
    price_max: float,
    price_currency: str,
    item_location_country: str | None = None,
    buying_options: str | None = None,
) -> str:
    parts = [
        f"price:[{price_min}..{price_max}]",
        f"priceCurrency:{price_currency}",
    ]
    if item_location_country:
        cc = item_location_country.strip().upper()
        parts.append(f"itemLocationCountry:{cc}")
    if buying_options:
        bo = buying_options.strip()
        if bo:
            parts.append(f"buyingOptions:{{{bo}}}")
    return ",".join(parts)


def search_item_summaries(
    access_token: str,
    *,
    marketplace_id: str,
    q: str,
    filter_expr: str,
    limit: int,
    offset: int,
    user_agent: str,
    timeout: float,
    category_ids: str | None = None,
) -> dict[str, Any]:
    params: dict[str, str] = {
        "sort": "newlyListed",
        "filter": filter_expr,
        "limit": str(limit),
        "offset": str(offset),
    }
    qs = (q or "").strip()
    if qs:
        params["q"] = qs
    if category_ids and category_ids.strip():
        params["category_ids"] = category_ids.strip()
    r = httpx.get(
        EBAY_BROWSE_SEARCH_URL,
        params=params,
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace_id.strip(),
            "User-Agent": user_agent,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("eBay search returned non-object JSON")
    return data


def request_url_for_log(
    marketplace_id: str,
    q: str,
    filter_expr: str,
    limit: int,
    offset: int,
    category_ids: str | None = None,
) -> str:
    p: dict[str, str] = {
        "sort": "newlyListed",
        "filter": filter_expr,
        "limit": str(limit),
        "offset": str(offset),
    }
    qs = (q or "").strip()
    if qs:
        p["q"] = qs
    if category_ids and category_ids.strip():
        p["category_ids"] = category_ids.strip()
    qstr = urlencode(p)
    return f"{EBAY_BROWSE_SEARCH_URL}?{qstr}#marketplace={marketplace_id}"

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from bikefinder.config import AppConfig, SearchEntry
from bikefinder.models import Listing
from bikefinder.sources.ebay.client import (
    DEFAULT_OAUTH_SCOPE,
    build_search_filter_parts,
    get_application_access_token,
    request_url_for_log,
    search_item_summaries,
)
from bikefinder.sources.ebay.parse import listings_from_ebay_search_response
from bikefinder.sources.listing_ids import with_source_prefix


def _entry_for_name(cfg: AppConfig, search_name: str) -> SearchEntry | None:
    for s in cfg.searches:
        if s.name == search_name:
            return s
    return None


def _oauth_scope() -> str:
    return (os.environ.get("EBAY_OAUTH_SCOPE") or "").strip() or DEFAULT_OAUTH_SCOPE


def _passes(entry: SearchEntry, cfg: AppConfig) -> list[tuple[str, str, str | None]]:
    """(marketplace_id, price_currency, item_location_country or None)."""
    mp_ids = list(entry.ebay_marketplace_ids or cfg.ebay_default_marketplace_ids)
    if not mp_ids:
        raise ValueError(
            "eBay search needs ebay_marketplace_ids on the search entry or "
            "ebay_default_marketplace_ids in config"
        )
    passes: list[tuple[str, str, str | None]] = []
    for mp in mp_ids:
        mid = mp.strip().upper()
        cur = "CHF" if mid == "EBAY_CH" else "EUR"
        passes.append((mid, cur, None))
    hub = (entry.ebay_location_hub_marketplace_id or "EBAY_IT").strip().upper()
    for cc in entry.ebay_location_countries:
        raw = cc.strip().upper()
        if len(raw) == 2:
            passes.append((hub, "EUR", raw))
    return passes


def _category_ids(entry: SearchEntry, cfg: AppConfig) -> str | None:
    per = (entry.ebay_category_ids or "").strip()
    if per:
        return per
    d = (cfg.ebay_default_category_ids or "").strip()
    return d or None


def _price_bounds(
    currency: str, cfg: AppConfig
) -> tuple[float, float]:
    if currency == "CHF":
        return (float(cfg.ebay_price_min_chf), float(cfg.ebay_price_max_chf))
    return (float(cfg.ebay_price_min_eur), float(cfg.ebay_price_max_eur))


class EbaySource:
    source_id = "ebay"
    display_name = "eBay (Browse API)"

    def fetch_search_pages(
        self,
        cfg: AppConfig,
        search_url: str,
        search_name: str,
        *,
        start_page_index: int = 0,
    ) -> tuple[list[Listing], bool]:
        _ = search_url
        entry = _entry_for_name(cfg, search_name)
        if entry is None:
            raise ValueError(f"No search named {search_name!r} in config")

        if entry.ebay_query is None:
            q = (cfg.ebay_default_query or "").strip()
        else:
            q = entry.ebay_query.strip()
        buying = (entry.ebay_buying_options or "").strip() or None
        cat_ids = _category_ids(entry, cfg)
        if not q and not (cat_ids and cat_ids.strip()):
            raise ValueError(
                f"eBay search {entry.name!r}: keywordless (category-only) mode needs "
                "ebay_category_ids on the entry or ebay_default_category_ids in config"
            )
        passes = _passes(entry, cfg)

        client_id = (os.environ.get("EBAY_CLIENT_ID") or "").strip()
        client_secret = (os.environ.get("EBAY_CLIENT_SECRET") or "").strip()
        if not client_id or not client_secret:
            raise RuntimeError(
                "eBay credentials missing: set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET in the environment"
            )

        token = get_application_access_token(
            client_id,
            client_secret,
            scope=_oauth_scope(),
            timeout=float(cfg.request_timeout_seconds),
        )
        limit = 200
        all_listings: list[Listing] = []
        reached_end = True
        first_req = True

        for marketplace_id, currency, loc_country in passes:
            pmin, pmax = _price_bounds(currency, cfg)
            filter_expr = build_search_filter_parts(
                price_min=pmin,
                price_max=pmax,
                price_currency=currency,
                item_location_country=loc_country,
                buying_options=buying,
            )
            pass_reached_end = False
            for page_idx in range(cfg.max_pages_per_search):
                if not first_req and cfg.delay_seconds > 0:
                    time.sleep(cfg.delay_seconds)
                first_req = False

                offset = (start_page_index + page_idx) * limit
                page_url = request_url_for_log(
                    marketplace_id, q, filter_expr, limit, offset, category_ids=cat_ids
                )
                payload = search_item_summaries(
                    token,
                    marketplace_id=marketplace_id,
                    q=q,
                    filter_expr=filter_expr,
                    limit=limit,
                    offset=offset,
                    user_agent=cfg.user_agent,
                    timeout=float(cfg.request_timeout_seconds),
                    category_ids=cat_ids,
                )
                chunk = listings_from_ebay_search_response(payload, search_name)
                summaries = payload.get("itemSummaries")
                n_raw = len(summaries) if isinstance(summaries, list) else 0

                for L in chunk:
                    L.search_page_url = page_url
                all_listings.extend(chunk)

                if n_raw < limit:
                    pass_reached_end = True
                    break

            if not pass_reached_end:
                reached_end = False

        seen_ids: set[str] = set()
        deduped: list[Listing] = []
        for L in all_listings:
            if L.listing_id in seen_ids:
                continue
            seen_ids.add(L.listing_id)
            deduped.append(L)

        return [with_source_prefix(L, self.source_id) for L in deduped], reached_end

    def parse_posted_at(self, value: str | None) -> datetime | None:
        if not value:
            return None
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

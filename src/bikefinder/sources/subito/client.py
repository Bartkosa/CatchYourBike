from __future__ import annotations

import time
from datetime import datetime
from typing import Callable
from urllib.parse import parse_qsl, urlparse

from curl_cffi import requests as curl_requests
from curl_cffi.requests.exceptions import HTTPError

from bikefinder.config import AppConfig
from bikefinder.models import Listing
from bikefinder.sources.subito.serp_html import (
    append_query,
    has_next_data_script,
    listings_from_search_html,
)

Fetcher = Callable[[str], str]


def _log_fetch_page(page_url: str, listing_count: int) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [FETCH_PAGE] listings={listing_count} url={page_url}", flush=True)


def _offset_from_url(url: str) -> int:
    q = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
    try:
        return int(q.get("o", "0") or 0)
    except ValueError:
        return 0


def _with_offset(base_url: str, offset: int, *, price_min_eur: int, price_max_eur: int) -> str:
    url = append_query(base_url, o=offset)
    return append_query(url, ps=price_min_eur, pe=price_max_eur)


def _browser_headers(user_agent: str, _url: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://www.subito.it/",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }


def _fetch_playwright(url: str, user_agent: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            'Playwright is not installed. Run: pip install ".[playwright]" && playwright install chromium'
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=user_agent)
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_selector("script#__NEXT_DATA__", timeout=20_000)
            except Exception:
                pass
            page.wait_for_timeout(500)
            return page.content()
        finally:
            browser.close()


def _curl_get(url: str, user_agent: str) -> str:
    """Subito often returns 403 to plain Python TLS; curl_cffi impersonates Chrome."""
    r = curl_requests.get(
        url,
        impersonate="chrome120",
        headers=_browser_headers(user_agent, url),
        timeout=60,
    )
    r.raise_for_status()
    return r.text


def fetch_search_pages(
    cfg: AppConfig,
    search_url: str,
    search_name: str,
    *,
    start_page_index: int = 0,
    fetcher: Fetcher | None = None,
) -> tuple[list[Listing], bool]:
    """Fetch one batch of pages. Returns (listings, reached_end)."""
    all_listings: list[Listing] = []
    session_warmed = False
    reached_end = False

    def default_fetch(u: str) -> str:
        nonlocal session_warmed
        if not session_warmed:
            _curl_get("https://www.subito.it/", cfg.user_agent)
            session_warmed = True
        return _curl_get(u, cfg.user_agent)

    fn = fetcher or default_fetch

    # Subito's `o` acts like a 1-based page index in the pagination UI:
    # page 1 => o=1, page 2 => o=2, page 3 => o=3, etc.
    base_offset = _offset_from_url(search_url) + start_page_index
    for page_idx in range(cfg.max_pages_per_search):
        offset = base_offset + page_idx
        url = _with_offset(
            search_url,
            offset,
            price_min_eur=cfg.subito_price_min_eur,
            price_max_eur=cfg.subito_price_max_eur,
        )
        html: str
        try:
            # Subito SSR includes __NEXT_DATA__; headless Playwright often gets a bot wall when
            # ``use_playwright`` is enabled for Wallapop. Always prefer curl_cffi first.
            html = fn(url)
        except HTTPError as e:
            resp = getattr(e, "response", None)
            # Past-last-page and some stale-pagination cases: Subito serves 404 instead of an empty SERP.
            if resp is not None and resp.status_code == 404:
                reached_end = True
                break
            if (
                resp is not None
                and resp.status_code == 403
                and cfg.use_playwright
            ):
                html = _fetch_playwright(url, cfg.user_agent)
            else:
                raise
        else:
            if not has_next_data_script(html) and cfg.use_playwright:
                ts = datetime.now().strftime("%H:%M:%S")
                print(
                    f"[{ts}] [FETCH_PAGE] Subito HTML missing __NEXT_DATA__ after curl; "
                    "retrying with Playwright",
                    flush=True,
                )
                html = _fetch_playwright(url, cfg.user_agent)
        listings, _total, total_pages = listings_from_search_html(html, search_name)
        # Attach the exact paginated URL that produced these listings.
        for l in listings:
            l.search_page_url = url
        _log_fetch_page(url, len(listings))
        all_listings.extend(listings)
        absolute_page_index = start_page_index + page_idx
        if absolute_page_index + 1 >= total_pages:
            reached_end = True
            break
        if page_idx < cfg.max_pages_per_search - 1 and cfg.delay_seconds > 0:
            time.sleep(cfg.delay_seconds)

    return all_listings, reached_end

from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

from curl_cffi import requests as curl_requests
from curl_cffi.requests.exceptions import HTTPError

from bikefinder.config import AppConfig
from bikefinder.models import Listing
from bikefinder.sources.kupujemprodajem.serp_html import (
    listings_from_search_html,
    serp_has_next_page,
)
from bikefinder.sources.kupujemprodajem.urls import (
    assert_kupujemprodajem_search_url,
    build_serp_url,
)

Fetcher = Callable[[str], str]

# KP sometimes returns gateway errors under load; retry before failing the crawl.
_TRANSIENT_HTTP = frozenset({429, 502, 503, 504})


def _kp_resilient_curl_then_playwright(
    url: str,
    cfg: AppConfig,
    do_curl: Fetcher,
) -> str:
    """Try curl a few times on transient errors, then Playwright (same as 403 path)."""
    for attempt in range(3):
        try:
            return do_curl(url)
        except HTTPError as e:
            resp = getattr(e, "response", None)
            code = resp.status_code if resp is not None else None
            if code == 403 and cfg.use_playwright:
                return _fetch_playwright(url, cfg.user_agent)
            if code in _TRANSIENT_HTTP and attempt < 2:
                time.sleep(max(2.0, float(cfg.delay_seconds)))
                continue
            if code in _TRANSIENT_HTTP and cfg.use_playwright:
                return _fetch_playwright(url, cfg.user_agent)
            raise


def _log_fetch_page(page_url: str, listing_count: int) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [FETCH_PAGE] listings={listing_count} url={page_url}", flush=True)


def _browser_headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "sr-RS,sr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://www.kupujemprodajem.com/",
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
                page.wait_for_function(
                    "() => document.documentElement.innerHTML.includes('AdItem_adHolder__')",
                    timeout=25_000,
                )
            except Exception:
                pass
            page.wait_for_timeout(500)
            return page.content()
        finally:
            browser.close()


def _curl_get(url: str, user_agent: str, timeout: float) -> str:
    r = curl_requests.get(
        url,
        impersonate="chrome120",
        headers=_browser_headers(user_agent),
        timeout=timeout,
    )
    r.raise_for_status()
    return r.text


def _page_reached_end(
    html: str,
    *,
    total_results: int,
    total_pages: int,
    absolute_page_1based: int,
) -> bool:
    if total_results > 0:
        return absolute_page_1based >= total_pages
    return not serp_has_next_page(html)


def fetch_search_pages(
    cfg: AppConfig,
    search_url: str,
    search_name: str,
    *,
    start_page_index: int = 0,
    fetcher: Fetcher | None = None,
) -> tuple[list[Listing], bool]:
    assert_kupujemprodajem_search_url(search_url)
    all_listings: list[Listing] = []
    session_warmed = False
    reached_end = False
    timeout = float(cfg.request_timeout_seconds)

    def default_fetch(u: str) -> str:
        nonlocal session_warmed
        if not session_warmed:
            _curl_get("https://www.kupujemprodajem.com/", cfg.user_agent, timeout)
            session_warmed = True
        return _curl_get(u, cfg.user_agent, timeout)

    for page_idx in range(cfg.max_pages_per_search):
        absolute_page_1based = start_page_index + page_idx + 1
        url = build_serp_url(
            search_url,
            price_min_eur=cfg.kupujemprodajem_price_min_eur,
            price_max_eur=cfg.kupujemprodajem_price_max_eur,
            page_1based=absolute_page_1based,
        )
        html: str
        if fetcher is not None:
            html = fetcher(url)
        else:
            html = _kp_resilient_curl_then_playwright(url, cfg, default_fetch)
        if not html.strip() and cfg.use_playwright:
            html = _fetch_playwright(url, cfg.user_agent)
        elif "AdItem_adHolder__" not in html and cfg.use_playwright:
            html = _fetch_playwright(url, cfg.user_agent)

        listings, total_results, total_pages = listings_from_search_html(
            html, search_name
        )
        for lst in listings:
            lst.search_page_url = url
        _log_fetch_page(url, len(listings))
        all_listings.extend(listings)

        if _page_reached_end(
            html,
            total_results=total_results,
            total_pages=total_pages,
            absolute_page_1based=absolute_page_1based,
        ):
            reached_end = True
            break

        if page_idx < cfg.max_pages_per_search - 1 and cfg.delay_seconds > 0:
            time.sleep(cfg.delay_seconds)

    return all_listings, reached_end

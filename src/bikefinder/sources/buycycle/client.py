from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from bikefinder.config import AppConfig
from bikefinder.models import Listing
from bikefinder.sources.buycycle.serp_dom import (
    card_snapshot_js,
    listings_from_snapshots,
)
from bikefinder.sources.buycycle.urls import assert_buycycle_search_url, build_serp_url

Fetcher = Callable[[str, int], tuple[list[Listing], bool]]


def _log_fetch_page(page_url: str, listing_count: int) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [FETCH_PAGE] listings={listing_count} url={page_url}", flush=True)


def _accept_cookiebot(page) -> None:
    try:
        loc = page.locator("#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll")
        loc.click(timeout=5000)
    except Exception:
        pass


def _scroll_listing_grid(page, *, rounds: int, pause_ms: float) -> None:
    for _ in range(max(1, rounds)):
        page.mouse.wheel(0, 3500)
        page.wait_for_timeout(int(pause_ms))


def _fetch_one_page_playwright(
    page_url: str,
    cfg: AppConfig,
    search_name: str,
    *,
    page_1based: int,
    crawl_epoch: datetime,
    minutes_per_page: int = 1440,
) -> list[Listing]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            'BuyCycle requires Playwright. Run: pip install ".[playwright]" && playwright install chromium'
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            pw_page = browser.new_page(
                user_agent=cfg.user_agent,
                locale="it-IT",
                viewport={"width": 1400, "height": 900},
            )
            pw_page.goto(page_url, wait_until="domcontentloaded", timeout=120_000)
            pw_page.wait_for_timeout(2000)
            _accept_cookiebot(pw_page)
            pw_page.wait_for_timeout(1500)
            _scroll_listing_grid(
                pw_page,
                rounds=16,
                pause_ms=max(250.0, min(cfg.delay_seconds * 500, 900.0)),
            )
            rows: list[dict[str, str]] = pw_page.locator(
                "[data-cnstrc-item-id]"
            ).evaluate_all(card_snapshot_js())
        finally:
            browser.close()

    def posted_at_for_index(idx: int) -> str | None:
        slot = (page_1based - 1) * minutes_per_page + idx
        dt = crawl_epoch - timedelta(minutes=slot)
        return dt.replace(microsecond=0).isoformat()

    return listings_from_snapshots(
        rows,
        search_name=search_name,
        search_page_url=page_url,
        posted_at_for_index=posted_at_for_index,
    )


def fetch_search_pages(
    cfg: AppConfig,
    search_url: str,
    search_name: str,
    *,
    start_page_index: int = 0,
    fetcher: Fetcher | None = None,
) -> tuple[list[Listing], bool]:
    assert_buycycle_search_url(search_url)
    all_listings: list[Listing] = []
    reached_end = False
    crawl_epoch = datetime.now(timezone.utc)

    def default_fetch(u: str, p1: int) -> tuple[list[Listing], bool]:
        L = _fetch_one_page_playwright(
            u,
            cfg,
            search_name,
            page_1based=p1,
            crawl_epoch=crawl_epoch,
        )
        return L, len(L) == 0

    fn: Fetcher = fetcher or default_fetch

    for page_idx in range(cfg.max_pages_per_search):
        absolute_page_1based = start_page_index + page_idx + 1
        url = build_serp_url(
            search_url,
            price_min_eur=cfg.buycycle_price_min_eur,
            price_max_eur=cfg.buycycle_price_max_eur,
            page_1based=absolute_page_1based,
        )
        listings, empty = fn(url, absolute_page_1based)
        for l in listings:
            l.search_page_url = url
        _log_fetch_page(url, len(listings))
        all_listings.extend(listings)

        if empty:
            reached_end = True
            break

        if page_idx < cfg.max_pages_per_search - 1 and cfg.delay_seconds > 0:
            time.sleep(cfg.delay_seconds)

    return all_listings, reached_end

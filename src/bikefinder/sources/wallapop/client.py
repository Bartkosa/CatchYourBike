from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from curl_cffi import requests as curl_requests
from curl_cffi.requests.exceptions import HTTPError

from bikefinder.config import AppConfig
from bikefinder.models import Listing
from bikefinder.sources.wallapop.parse_api import (
    _extract_items,
    _extract_next_cursor,
    listings_from_wallapop_payload,
)

WALLAPOP_SEARCH_API = "https://api.wallapop.com/api/v3/general/search"
WALLAPOP_ALLOWED_URL = (
    "https://it.wallapop.com/search?category_id=17000&min_sale_price=200&max_sale_price=1000&order_by=newest"
)
_REQUIRED_QUERY = {
    "category_id": "17000",
    "order_by": "newest",
}


def _log_fetch_page(
    page_url: str, listing_count: int, *, chunk_index: int | None = None
) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    extra = f" chunk={chunk_index}" if chunk_index is not None else ""
    print(
        f"[{ts}] [FETCH_PAGE] listings={listing_count} url={page_url}{extra}",
        flush=True,
    )


def _assert_supported_search_url(search_url: str) -> None:
    allowed = WALLAPOP_ALLOWED_URL
    parsed = urlparse(search_url)
    allowed_parsed = urlparse(allowed)
    if parsed.scheme != allowed_parsed.scheme or parsed.netloc != allowed_parsed.netloc:
        raise ValueError(f"Wallapop v1 supports only this URL: {allowed}")
    if parsed.path.rstrip("/") != allowed_parsed.path.rstrip("/"):
        raise ValueError(f"Wallapop v1 supports only this URL: {allowed}")
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, expected_value in _REQUIRED_QUERY.items():
        if q.get(key) != expected_value:
            raise ValueError(f"Wallapop v1 supports only this URL: {allowed}")
    extra_nonempty = {
        key: value
        for key, value in q.items()
        if key not in _REQUIRED_QUERY and value.strip()
    }
    if "keywords" in extra_nonempty:
        raise ValueError(f"Wallapop v1 supports only this URL: {allowed}")


def _browser_headers(cfg: AppConfig, page_url: str) -> dict[str, str]:
    return {
        "User-Agent": cfg.user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": page_url,
        "Origin": "https://it.wallapop.com",
    }


def _append_query(url: str, **params: str | int) -> str:
    u = urlparse(url)
    q = dict(parse_qsl(u.query, keep_blank_values=True))
    for key, value in params.items():
        q[key] = str(value)
    return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(q), u.fragment))


def _dedupe_listings(listings: list[Listing]) -> list[Listing]:
    seen: set[str] = set()
    out: list[Listing] = []
    for listing in listings:
        if listing.listing_id in seen:
            continue
        seen.add(listing.listing_id)
        out.append(listing)
    return out


def _raw_listing_ids_from_payload(payload: dict) -> frozenset[str]:
    ids: set[str] = set()
    for item in _extract_items(payload):
        if not isinstance(item, dict):
            continue
        for key in ("id", "item_id"):
            raw = item.get(key)
            if raw is not None and str(raw).strip():
                ids.add(str(raw).strip())
                break
    return frozenset(ids)


def _url_without_page_param(url: str) -> str:
    """Drop ``page`` so the SERP matches the site (infinite scroll, not numbered pages)."""
    u = urlparse(url)
    q = [
        (k, v)
        for k, v in parse_qsl(u.query, keep_blank_values=True)
        if k != "page"
    ]
    return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(q), u.fragment))


def _synthetic_section_payload(
    new_items: list[dict], template: dict | None
) -> dict:
    """Build JSON like Wallapop's section response so ``listings_from_wallapop_payload`` works."""
    out: dict[str, object] = {"data": {"section": {"items": new_items}}}
    if template is not None:
        meta = template.get("meta")
        if isinstance(meta, dict):
            out["meta"] = meta
    return out


def _new_items_from_captured_range(
    captured: list[dict],
    start: int,
    end: int,
    known_ids: set[str],
) -> tuple[list[dict], dict | None]:
    """Collect listing dicts from section payloads in ``captured[start:end]`` not yet in ``known_ids``."""
    new_items: list[dict] = []
    last_pl: dict | None = None
    for pl in captured[start:end]:
        if not isinstance(pl, dict):
            continue
        items = _extract_items(pl)
        if not items:
            continue
        last_pl = pl
        for item in items:
            if not isinstance(item, dict):
                continue
            iid: str | None = None
            for key in ("id", "item_id"):
                raw = item.get(key)
                if raw is not None and str(raw).strip():
                    iid = str(raw).strip()
                    break
            if not iid or iid in known_ids:
                continue
            known_ids.add(iid)
            new_items.append(item)
    return new_items, last_pl


# Wallapop SERP "load more" label (it.wallapop.com); shadow DOM under ``<walla-button>``.
_WALLAPOP_LOAD_MORE_PATTERN = re.compile(
    r"Carica altro|Cargar más|Load more|Charger plus",
    re.IGNORECASE,
)


def _scroll_search_feed(page, scroll_wait_ms: int) -> None:
    """Fallback: scroll when the explicit load-more control is missing."""
    page.evaluate(
        """() => {
            const el = document.scrollingElement || document.documentElement;
            const h = el ? el.scrollHeight : 0;
            window.scrollTo(0, h);
        }"""
    )
    page.wait_for_timeout(scroll_wait_ms)
    for _ in range(4):
        page.mouse.wheel(0, 1600)
        page.wait_for_timeout(350)
    try:
        page.keyboard.press("End")
    except Exception:
        pass
    page.wait_for_timeout(max(400, scroll_wait_ms // 2))


def _js_click_wallapop_load_more(page) -> bool:
    """Click **Carica altro** via ``shadowRoot`` (reliable when Playwright locators miss)."""
    try:
        return bool(
            page.evaluate(
                r"""() => {
                    const hosts = document.querySelectorAll("walla-button");
                    for (const h of hosts) {
                        const root = h.shadowRoot;
                        if (!root) continue;
                        const btn = root.querySelector(
                            "button[part='button'], button.walla-button__button, button[type='button']"
                        );
                        if (!btn) continue;
                        const text = (btn.textContent || "").trim();
                        if (!/Carica altro|Cargar más|Load more|Charger plus/i.test(text))
                            continue;
                        if (btn.getAttribute("aria-disabled") === "true") continue;
                        btn.click();
                        return true;
                    }
                    return false;
                }"""
            )
        )
    except Exception:
        return False


def _click_wallapop_load_more(page, *, click_timeout_ms: int, after_click_wait_ms: int) -> bool:
    """
    Click the SERP **Carica altro** control (``walla-button`` + shadow root).

    Tries Playwright role/locator first, then a JS ``shadowRoot`` click.
    """
    # Scroll container into view (class hash changes; match stable substring).
    try:
        page.locator('[class*="loadMore"]').first.scroll_into_view_if_needed(
            timeout=click_timeout_ms
        )
    except Exception:
        pass

    page.wait_for_timeout(900)

    def _section_predicate(resp) -> bool:
        try:
            return (
                "/api/v3/search/section" in resp.url
                and resp.status == 200
            )
        except Exception:
            return False

    try:
        btn = page.get_by_role("button", name=_WALLAPOP_LOAD_MORE_PATTERN)
        if btn.count() > 0:
            first = btn.first
            first.wait_for(state="visible", timeout=click_timeout_ms)
            disabled = first.get_attribute("aria-disabled")
            if disabled and disabled.strip().lower() == "true":
                pass
            else:
                try:
                    with page.expect_response(
                        _section_predicate,
                        timeout=click_timeout_ms,
                    ):
                        first.click(timeout=click_timeout_ms)
                except Exception:
                    first.click(timeout=click_timeout_ms)
                page.wait_for_timeout(after_click_wait_ms)
                return True
    except Exception:
        pass

    try:
        inner = page.locator("walla-button").locator("button").filter(
            has_text=_WALLAPOP_LOAD_MORE_PATTERN
        )
        if inner.count() > 0:
            b = inner.first
            b.wait_for(state="visible", timeout=min(5_000, click_timeout_ms))
            try:
                with page.expect_response(
                    _section_predicate,
                    timeout=click_timeout_ms,
                ):
                    b.click(timeout=click_timeout_ms)
            except Exception:
                b.click(timeout=click_timeout_ms)
            page.wait_for_timeout(after_click_wait_ms)
            return True
    except Exception:
        pass

    if _js_click_wallapop_load_more(page):
        page.wait_for_timeout(after_click_wait_ms)
        return True

    return False


def _wait_until_captured_grows(
    captured: list[dict],
    prev_len: int,
    page,
    *,
    max_wait_ms: int,
) -> bool:
    """Poll until the response handler appended payloads (or timeout)."""
    if len(captured) > prev_len:
        return True
    deadline = time.monotonic() + max_wait_ms / 1000.0
    while time.monotonic() < deadline:
        page.wait_for_timeout(200)
        if len(captured) > prev_len:
            return True
    return len(captured) > prev_len


def _wallapop_listing_json_url(url: str) -> bool:
    """True if this looks like a Wallapop search/listings JSON response."""
    if "api.wallapop.com" not in url:
        return False
    if "/api/v3/search/section" in url:
        return True
    if "/api/v3/general/search" in url:
        return True
    return False


def _playwright_fetch_general_search(
    context,
    *,
    search_url: str,
    canonical_search_url: str,
    user_agent: str,
    timeout_ms: int,
    next_page: str | None = None,
    page: str | None = None,
) -> dict | None:
    """
    Same-origin API call using the browser context cookie jar (works when the SPA
    stops emitting section JSON but ``general/search`` still paginates).
    """
    q = dict(parse_qsl(urlparse(search_url).query, keep_blank_values=True))
    q.pop("page", None)
    if page is not None:
        q["page"] = page
    if next_page:
        q["next_page"] = next_page
    try:
        r = context.request.get(
            WALLAPOP_SEARCH_API,
            params=q,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json, text/plain, */*",
                "Referer": canonical_search_url,
                "Origin": "https://it.wallapop.com",
            },
            timeout=timeout_ms,
        )
        if r.status != 200:
            return None
        body = r.json()
        if not isinstance(body, dict):
            return None
        if not _extract_items(body):
            return None
        return body
    except Exception:
        return None


def _load_more_wallapop_search(
    page,
    captured: list[dict],
    *,
    click_timeout_ms: int,
    scroll_wait_ms: int,
) -> None:
    """Primary: **Carica altro** (retry clicks); wait for new JSON; fallback: scroll."""
    prev_len = len(captured)
    wait_grow = min(20_000, click_timeout_ms + 10_000)

    for _attempt in range(4):
        clicked = _click_wallapop_load_more(
            page,
            click_timeout_ms=click_timeout_ms,
            after_click_wait_ms=max(1000, scroll_wait_ms // 2),
        )
        if clicked and _wait_until_captured_grows(
            captured,
            prev_len,
            page,
            max_wait_ms=wait_grow,
        ):
            page.wait_for_timeout(500)
            return
        if _js_click_wallapop_load_more(page):
            if _wait_until_captured_grows(
                captured,
                prev_len,
                page,
                max_wait_ms=12_000,
            ):
                page.wait_for_timeout(500)
                return
        page.wait_for_timeout(700)

    _scroll_search_feed(page, scroll_wait_ms)
    _wait_until_captured_grows(
        captured,
        prev_len,
        page,
        max_wait_ms=10_000,
    )


def _pick_last_section_payload(captured: list[dict]) -> dict | None:
    """
    After each navigation, Wallapop may emit several ``/search/section`` responses.
    The *first* one is often stale (previous page). Prefer the *last* payload that
    has a non-empty ``data.section.items`` list.
    """
    for pl in reversed(captured):
        if not isinstance(pl, dict):
            continue
        data = pl.get("data")
        if not isinstance(data, dict):
            continue
        section = data.get("section")
        if not isinstance(section, dict):
            continue
        items = section.get("items")
        if isinstance(items, list) and items:
            return pl
    return None


def _playwright_fetch_section_pages(
    *,
    user_agent: str,
    request_timeout_seconds: float,
    search_url: str,
    start_page_index: int,
    num_pages: int,
    delay_seconds: float,
) -> list[tuple[str, dict]]:
    """
    One browser session: load the search once, then scroll like the live site.

    Strategy: (1) capture JSON from **/search/section** and **/general/search**
    network responses; (2) click **Carica altro** (with retries) and scroll fallback;
    (3) if the UI adds no new JSON, call **/api/v3/general/search** via
    ``context.request`` using the same cookies — ``meta.next_page`` or ``page=2``….

    The live site only loads the canonical search URL (no ``page=``, no ``#``);
    we ``goto`` that URL only. ``start_page_index`` is kept for API compatibility
    but **ignored** here—outer crawl batches may repeat the same surface (dedupe
    + DB frontiers still apply). ``num_pages`` is the max load-more *chunks*.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            'Playwright is not installed. Run: pip install ".[playwright]" && playwright install chromium'
        ) from e

    # Crawl passes batch offsets; the live Wallapop SERP has no ``page=`` query.
    _ = start_page_index

    timeout_ms = int(max(5.0, request_timeout_seconds) * 1000)
    out: list[tuple[str, dict]] = []
    scroll_wait_ms = 2200

    def _no_cache_route(route) -> None:
        headers = dict(route.request.headers)
        headers["Cache-Control"] = "no-cache"
        headers["Pragma"] = "no-cache"
        route.continue_(headers=headers)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1280, "height": 720},
            )
            context.route("https://api.wallapop.com/**", _no_cache_route)
            context.route("https://it.wallapop.com/**", _no_cache_route)
            page = context.new_page()
            captured: list[dict] = []

            def _on_response(resp) -> None:
                try:
                    if resp.status != 200:
                        return
                    if not _wallapop_listing_json_url(resp.url):
                        return
                    payload = resp.json()
                except Exception:
                    return
                if isinstance(payload, dict) and _extract_items(payload):
                    captured.append(payload)

            page.on("response", _on_response)

            # Same URL as in the browser — Wallapop does not use ?page= on search.
            canonical_search_url = _url_without_page_param(search_url)

            captured.clear()
            page.goto(canonical_search_url, wait_until="load", timeout=timeout_ms)
            # Hydrate web components (``walla-button``) before **Carica altro** exists.
            page.wait_for_timeout(3800)

            known_ids: set[str] = set()
            slice_mark = 0
            last_items_payload: dict | None = None
            api_page_next = 2

            for round_idx in range(num_pages):
                slice_end = len(captured)
                new_items, template_pl = _new_items_from_captured_range(
                    captured, slice_mark, slice_end, known_ids
                )

                if not new_items and round_idx == 0:
                    pl = _pick_last_section_payload(captured)
                    if pl is not None:
                        template_pl = pl
                        for item in _extract_items(pl):
                            if not isinstance(item, dict):
                                continue
                            iid = None
                            for key in ("id", "item_id"):
                                raw = item.get(key)
                                if raw is not None and str(raw).strip():
                                    iid = str(raw).strip()
                                    break
                            if iid and iid not in known_ids:
                                known_ids.add(iid)
                                new_items.append(item)

                if not new_items and round_idx > 0:
                    appended = False
                    cur = (
                        _extract_next_cursor(last_items_payload)
                        if last_items_payload is not None
                        else None
                    )
                    if cur:
                        pl = _playwright_fetch_general_search(
                            context,
                            search_url=search_url,
                            canonical_search_url=canonical_search_url,
                            user_agent=user_agent,
                            timeout_ms=timeout_ms,
                            next_page=cur,
                            page=None,
                        )
                        if pl is not None:
                            captured.append(pl)
                            appended = True
                    if not appended:
                        pl = _playwright_fetch_general_search(
                            context,
                            search_url=search_url,
                            canonical_search_url=canonical_search_url,
                            user_agent=user_agent,
                            timeout_ms=timeout_ms,
                            next_page=None,
                            page=str(api_page_next),
                        )
                        if pl is not None:
                            captured.append(pl)
                            api_page_next += 1
                            appended = True
                    if appended:
                        slice_end = len(captured)
                        new_items, template_pl = _new_items_from_captured_range(
                            captured, slice_mark, slice_end, known_ids
                        )

                if not new_items:
                    break

                out.append(
                    (
                        canonical_search_url,
                        _synthetic_section_payload(new_items, template_pl),
                    )
                )

                last_items_payload = template_pl
                slice_mark = len(captured)
                if round_idx >= num_pages - 1:
                    break

                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                _load_more_wallapop_search(
                    page,
                    captured,
                    click_timeout_ms=min(25_000, max(timeout_ms, 20_000)),
                    scroll_wait_ms=scroll_wait_ms,
                )
        finally:
            browser.close()

    return out


def fetch_search_pages(
    cfg: AppConfig,
    search_url: str,
    search_name: str,
    *,
    start_page_index: int = 0,
) -> tuple[list[Listing], bool]:
    """Fetch Wallapop listings and return (listings, reached_end)."""
    _assert_supported_search_url(search_url)
    query = dict(parse_qsl(urlparse(search_url).query, keep_blank_values=True))
    all_listings: list[Listing] = []
    reached_end = False

    if cfg.use_playwright:
        pairs = _playwright_fetch_section_pages(
            user_agent=cfg.user_agent,
            request_timeout_seconds=cfg.request_timeout_seconds,
            search_url=search_url,
            start_page_index=start_page_index,
            num_pages=cfg.max_pages_per_search,
            delay_seconds=cfg.delay_seconds,
        )
        for chunk_i, (page_url, payload) in enumerate(pairs):
            listings, _ = listings_from_wallapop_payload(payload, search_name)
            for listing in listings:
                listing.search_page_url = page_url
            _log_fetch_page(page_url, len(listings), chunk_index=chunk_i)
            all_listings.extend(listings)

        # Outer `crawl` loop advances `start_page_index` when `reached_end` is False.
        # A full batch means we got `max_pages_per_search` scroll chunks; fewer means
        # the feed had no more new items (or Playwright failed to trigger more loads).
        if not pairs:
            reached_end = True
        elif len(pairs) < cfg.max_pages_per_search:
            reached_end = True
        else:
            reached_end = False

        return _dedupe_listings(all_listings), reached_end

    session = curl_requests.Session(impersonate="chrome120")
    session.get(
        "https://it.wallapop.com/",
        headers={"User-Agent": cfg.user_agent},
        timeout=cfg.request_timeout_seconds,
    )

    next_cursor: str | None = None
    canonical_search_url = _url_without_page_param(search_url)
    for page_idx in range(start_page_index, start_page_index + cfg.max_pages_per_search):
        page_params = dict(query)
        page_params["page"] = str(page_idx + 1)
        if next_cursor:
            page_params["next_page"] = next_cursor

        try:
            resp = session.get(
                WALLAPOP_SEARCH_API,
                params=page_params,
                headers=_browser_headers(cfg, canonical_search_url),
                timeout=cfg.request_timeout_seconds,
            )
            resp.raise_for_status()
            payload = resp.json()
        except HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in {404}:
                reached_end = True
                break
            if status == 403:
                pairs = _playwright_fetch_section_pages(
                    user_agent=cfg.user_agent,
                    request_timeout_seconds=cfg.request_timeout_seconds,
                    search_url=search_url,
                    start_page_index=page_idx,
                    num_pages=cfg.max_pages_per_search - (page_idx - start_page_index),
                    delay_seconds=cfg.delay_seconds,
                )
                for ci, (p_url, pl) in enumerate(pairs):
                    listings, _ = listings_from_wallapop_payload(pl, search_name)
                    for listing in listings:
                        listing.search_page_url = p_url
                    _log_fetch_page(p_url, len(listings), chunk_index=ci)
                    all_listings.extend(listings)
                num_requested = cfg.max_pages_per_search - (
                    page_idx - start_page_index
                )
                reached_end = not pairs or len(pairs) < num_requested
                return _dedupe_listings(all_listings), reached_end
            raise

        listings, new_cursor = listings_from_wallapop_payload(payload, search_name)
        for listing in listings:
            listing.search_page_url = canonical_search_url
        _log_fetch_page(
            canonical_search_url,
            len(listings),
            chunk_index=page_idx - start_page_index,
        )
        all_listings.extend(listings)

        if not listings:
            reached_end = True
            break
        if not new_cursor or new_cursor == next_cursor:
            reached_end = True
            break

        next_cursor = new_cursor
        if page_idx < start_page_index + cfg.max_pages_per_search - 1 and cfg.delay_seconds > 0:
            time.sleep(cfg.delay_seconds)

    return _dedupe_listings(all_listings), reached_end

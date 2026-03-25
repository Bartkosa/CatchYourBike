from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bikefinder.config import AppConfig
from bikefinder.models import Listing
from bikefinder.sources.facebook.graphql_harvest import harvest_marketplace_hints
from bikefinder.sources.facebook.id_harvest import (
    harvest_listing_ids_from_json,
    harvest_listing_ids_from_text,
)
from bikefinder.sources.facebook.parse import listings_from_cards
from bikefinder.sources.facebook.urls import build_fetch_url, simplify_np_marketplace_url


def _env_flag(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _facebook_nav_failed_message(exc: BaseException, page_url: str) -> str:
    hint = (
        "\n\nFacebook blocked automated navigation (redirect loop, login wall, or headless detection).\n"
        "Try in order:\n\n"
        "1) Persistent profile (best on Windows)\n"
        "   Create a folder, e.g. C:\\Users\\YOU\\fb_playwright_profile\n"
        "   PowerShell:\n"
        "     $env:FACEBOOK_BROWSER_USER_DATA_DIR = \"C:\\\\Users\\\\YOU\\\\fb_playwright_profile\"\n"
        "     .\\.venv\\Scripts\\python.exe scripts\\facebook_open_profile.py\n"
        "   Log in when the browser opens, then press Enter in the terminal.\n"
        "   Later runs: keep the same FACEBOOK_BROWSER_USER_DATA_DIR (headless works after login).\n\n"
        "2) Use installed Google Chrome instead of bundled Chromium\n"
        "     $env:FACEBOOK_USE_SYSTEM_CHROME = \"1\"\n\n"
        "3) One headed crawl so you can complete checkpoints in the window\n"
        "     $env:FACEBOOK_HEADFUL = \"1\"\n\n"
        "4) Or export cookies to Playwright storage_state.json → FACEBOOK_STORAGE_STATE_PATH in .env\n\n"
        f"Failed URL: {page_url if len(page_url) <= 160 else page_url[:150] + '…'}\n"
        f"Original error: {exc!r}"
    )
    return hint


# Collect listing links from the live Marketplace grid (DOM changes often; this is best-effort).
# Images usually live in the card tile (sibling of <a>), not inside the anchor — use card root heuristics.
_CARD_SNAPSHOT_JS = r"""
() => {
  const idRe = /\/marketplace\/item\/(\d{10,20})\b/;
  const out = [];
  const seen = new Set();
  function resolveHref(el) {
    let h = el.getAttribute("href");
    if (!h) return "";
    try {
      return new URL(h, location.href).href;
    } catch (e) {
      return h;
    }
  }
  function normSrc(s) {
    return (s || "").replace(/&amp;/g, "&").trim();
  }
  function isCdnUrl(s) {
    const u = normSrc(s).toLowerCase();
    return u.indexOf("fbcdn.net") >= 0 || u.indexOf("scontent") >= 0;
  }
  /** Smallest ancestor that still contains only this listing link (approx. one SERP tile). */
  function cardRootForListingLink(link) {
    let el = link.parentElement;
    let best = link;
    for (let i = 0; i < 20 && el; i++) {
      let n = 0;
      try {
        n = el.querySelectorAll('a[href*="/marketplace/item/"]').length;
      } catch (e) {}
      if (n > 1) return best;
      best = el;
      el = el.parentElement;
    }
    return best;
  }
  function collectCdnImages(rootEl) {
    const urls = [];
    const useen = new Set();
    rootEl.querySelectorAll("img[src]").forEach((img) => {
      const raw = img.getAttribute("src");
      if (!raw || urls.length >= 8) return;
      const n = normSrc(raw);
      if (!isCdnUrl(raw) || useen.has(n)) return;
      useen.add(n);
      urls.push(n);
    });
    return urls;
  }
  function addFromHref(href, rootEl) {
    if (!href || href.indexOf("marketplace/item") < 0) return;
    const m = href.match(idRe);
    if (!m) return;
    const id = m[1];
    if (seen.has(id)) return;
    seen.add(id);
    const aria = (rootEl.getAttribute("aria-label") || "").trim();
    const text = (rootEl.innerText || "").replace(/\s+/g, " ").trim();
    const title = aria || (text ? text.split("\n").map(s => s.trim()).filter(Boolean)[0] : "")
      || ("Item " + id);
    const card = cardRootForListingLink(rootEl);
    let imgs = collectCdnImages(rootEl);
    if (imgs.length === 0) imgs = collectCdnImages(card);
    out.push({ id, href, title, price: "", location: "", image_urls: imgs });
  }
  document.querySelectorAll("a").forEach((a) => addFromHref(resolveHref(a), a));
  document.querySelectorAll("[href*='marketplace/item']").forEach((el) => {
    addFromHref(resolveHref(el), el);
  });
  return out;
}
"""

# Scroll document + best-effort inner feed containers (Marketplace is often a nested scroller).
_FEED_NUDGE_SCROLL_JS = r"""
() => {
  const root = document.scrollingElement || document.documentElement;
  if (root) {
    root.scrollTo(0, root.scrollHeight);
  }
  const candidates = document.querySelectorAll(
    '[role="main"] div, main div, [data-pagelet] div, #scrollview div'
  );
  candidates.forEach((el) => {
    if (!(el instanceof HTMLElement)) return;
    const extra = el.scrollHeight - el.clientHeight;
    if (extra < 400) return;
    el.scrollTop = el.scrollHeight;
  });
  return true;
}
"""


def _log_fetch_page(page_url: str, listing_count: int) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [FETCH_PAGE] listings={listing_count} url={page_url}", flush=True)


def _merge_graphql_into_cards(
    cards: list[dict[str, Any]], graphql_by_id: dict[str, dict[str, Any]]
) -> None:
    for c in cards:
        lid = str(c.get("id") or "")
        g = graphql_by_id.get(lid)
        if not g:
            continue
        if g.get("creation_ts") is not None:
            c["creation_ts"] = g["creation_ts"]
        if g.get("title"):
            t = str(g["title"]).strip()
            if len(t) > len(str(c.get("title") or "")):
                c["title"] = t
        if g.get("price"):
            c["price"] = str(g["price"]).strip()
        for u in g.get("image_urls") or []:
            s = str(u).strip()
            if not s:
                continue
            lst = c.setdefault("image_urls", [])
            if s not in lst:
                lst.append(s)


def _fetch_batch_playwright(
    page_url: str,
    cfg: AppConfig,
    search_name: str,
    *,
    start_page_index: int,
    crawl_epoch: datetime,
) -> tuple[list[Listing], bool, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            'Facebook Marketplace requires Playwright. Run: pip install ".[playwright]" '
            "&& playwright install chromium"
        ) from e

    graphql_by_id: dict[str, dict[str, Any]] = {}
    graphql_raw_ids: set[str] = set()
    trace_responses = _env_flag("FACEBOOK_TRACE_RESPONSES")
    trace_printed: list[int] = [0]
    scan_stats: dict[str, int] = {
        "xhr_fetch_bodies": 0,
        "document_bodies": 0,
    }

    def _try_facebook_json(body: str) -> Any | None:
        s = body.lstrip("\ufeff \n\r\t")
        if s.startswith("for (;;);"):
            s = s[9:].lstrip()
        if not s or s[0] not in "{[":
            return None
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    _SKIP_SUFFIXES = (
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".ico",
        ".woff",
        ".woff2",
        ".mp4",
        ".js",
        ".css",
    )

    def on_response(response) -> None:
        try:
            if response.status != 200:
                return
            url = response.url
            ul = url.lower()
            path = ul.split("?", 1)[0]
            if path.endswith(_SKIP_SUFFIXES):
                return
            if "facebook.com" not in ul:
                return
            req = response.request
            rt = req.resource_type
            if rt not in ("xhr", "fetch", "document"):
                return
            cl = (response.headers.get("content-length") or "").strip()
            if cl.isdigit() and int(cl) > 12_000_000:
                return
            max_len = 4_000_000 if rt in ("xhr", "fetch") else 8_000_000
            body = response.text()
            if not body or len(body) > max_len:
                return

            found_here: set[str] = set()
            if rt in ("xhr", "fetch"):
                # Feed JSON often omits literal marketplace/item URLs; regex + JSON walk.
                scan_stats["xhr_fetch_bodies"] += 1
                for lid in harvest_listing_ids_from_text(body):
                    graphql_raw_ids.add(lid)
                    found_here.add(lid)
                payload = _try_facebook_json(body)
                if payload is not None:
                    try:
                        harvest_marketplace_hints(payload, graphql_by_id)
                    except (TypeError, ValueError):
                        pass
                    for lid in harvest_listing_ids_from_json(payload):
                        graphql_raw_ids.add(lid)
                        found_here.add(lid)
            else:
                low = body.lower()
                if "marketplace" not in low and "commerce" not in low:
                    return
                scan_stats["document_bodies"] += 1
                for lid in harvest_listing_ids_from_text(body):
                    graphql_raw_ids.add(lid)
                    found_here.add(lid)
                payload = _try_facebook_json(body)
                if payload is not None:
                    try:
                        harvest_marketplace_hints(payload, graphql_by_id)
                    except (TypeError, ValueError):
                        pass
                    for lid in harvest_listing_ids_from_json(payload):
                        graphql_raw_ids.add(lid)
                        found_here.add(lid)

            if trace_responses and trace_printed[0] < 35 and rt in ("xhr", "fetch"):
                if found_here or len(body) < 400_000:
                    trace_printed[0] += 1
                    _ts = datetime.now().strftime("%H:%M:%S")
                    print(
                        f"[{_ts}] [FETCH_PAGE][trace] {rt} ids+{len(found_here)} "
                        f"len={len(body)} url={url[:140]}",
                        flush=True,
                    )
        except Exception:
            return

    scroll_budget = 5 + (start_page_index + max(1, cfg.max_pages_per_search)) * 3
    scroll_budget = min(scroll_budget, cfg.facebook_scroll_rounds_cap)
    stale_threshold = cfg.facebook_stale_rounds
    min_scroll_rounds = cfg.facebook_min_scroll_rounds
    pause_ms = int(max(350, min(cfg.delay_seconds * 600, 1200)))

    storage_path = (os.environ.get("FACEBOOK_STORAGE_STATE_PATH") or "").strip()
    if storage_path and not Path(storage_path).is_file():
        _warn_ts = datetime.now().strftime("%H:%M:%S")
        print(
            f"[{_warn_ts}] [FETCH_PAGE] FACEBOOK_STORAGE_STATE_PATH={storage_path!r} "
            "not found; continuing without saved session (login wall likely).",
            flush=True,
        )
        storage_path = ""

    ordered_cards: list[dict[str, Any]] = []
    card_by_id: dict[str, dict[str, Any]] = {}
    stale_rounds = 0
    stopped_by_stale_break = False

    user_data_dir = (os.environ.get("FACEBOOK_BROWSER_USER_DATA_DIR") or "").strip()
    if user_data_dir:
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

    headless = not _env_flag("FACEBOOK_HEADFUL")
    use_system_chrome = _env_flag("FACEBOOK_USE_SYSTEM_CHROME")
    channel = "chrome" if use_system_chrome else None

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ]

    resolved_nav_url: dict[str, str] = {"url": page_url}

    def _attach_and_navigate(page) -> None:
        page.on("response", on_response)
        path_lc = (urlparse(page_url).path or "").lower()
        is_np = "/marketplace/np/" in path_lc
        # Warm-up can interact badly with np/ + partner params; open target directly.
        if not is_np:
            try:
                home = urlparse(page_url)
                origin = f"{home.scheme}://{home.netloc}/"
                page.goto(origin, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(1200)
            except Exception:
                pass

        if is_np:
            simple = simplify_np_marketplace_url(page_url)
            try_urls = [simple, page_url] if simple != page_url else [page_url]
        else:
            try_urls = [page_url]

        for idx, target in enumerate(try_urls):
            try:
                try:
                    page.goto(target, wait_until="load", timeout=90_000)
                except Exception:
                    page.goto(target, wait_until="domcontentloaded", timeout=120_000)
                resolved_nav_url["url"] = target
                if idx > 0:
                    _ts = datetime.now().strftime("%H:%M:%S")
                    print(
                        f"[{_ts}] [FETCH_PAGE] Facebook np/ URL: succeeded with fallback URL "
                        f"(attempt {idx + 1}/{len(try_urls)}).",
                        flush=True,
                    )
                break
            except Exception as e:
                redir = "TOO_MANY_REDIRECTS" in str(e) or "ERR_TOO_MANY_REDIRECTS" in str(
                    e
                )
                if redir and idx + 1 < len(try_urls):
                    _ts = datetime.now().strftime("%H:%M:%S")
                    print(
                        f"[{_ts}] [FETCH_PAGE] Facebook redirect loop on np/ URL; "
                        f"retrying simplified query (attempt {idx + 2}/{len(try_urls)}).",
                        flush=True,
                    )
                    continue
                raise RuntimeError(
                    _facebook_nav_failed_message(e, target)
                ) from e

        page.wait_for_timeout(3500)

    def _scroll_phase(page) -> None:
        nonlocal stale_rounds, stopped_by_stale_break

        def _nudge_scroll() -> None:
            try:
                page.evaluate(_FEED_NUDGE_SCROLL_JS)
            except Exception:
                pass

        # Let React hydrate and trigger first feed requests before counting "stale".
        for hi in range(8):
            page.mouse.wheel(0, 900)
            if hi == 3:
                _nudge_scroll()
            page.wait_for_timeout(min(450, pause_ms))
        page.wait_for_timeout(600)
        _nudge_scroll()
        page.wait_for_timeout(min(400, pause_ms))

        prev_dom_count = 0
        prev_xhr_ids = 0
        for round_i in range(scroll_budget):
            raw: list[dict[str, Any]] = page.evaluate(_CARD_SNAPSHOT_JS)
            for c in raw:
                lid = str(c.get("id") or "")
                if not lid:
                    continue
                if lid not in card_by_id:
                    card_by_id[lid] = dict(c)
                    ordered_cards.append(card_by_id[lid])
                else:
                    cur = card_by_id[lid]
                    for img in c.get("image_urls") or []:
                        if img and img not in cur.setdefault("image_urls", []):
                            cur["image_urls"].append(img)
                    nt = (c.get("title") or "").strip()
                    if len(nt) > len((cur.get("title") or "").strip()):
                        cur["title"] = nt

            n = len(ordered_cards)
            xhr_n = len(graphql_raw_ids)
            dom_grew = n > prev_dom_count
            xhr_grew = xhr_n > prev_xhr_ids
            if dom_grew or xhr_grew:
                stale_rounds = 0
            else:
                stale_rounds += 1
            prev_dom_count = n
            prev_xhr_ids = xhr_n

            rounds_done = round_i + 1
            if stale_rounds >= stale_threshold:
                if rounds_done >= min_scroll_rounds:
                    stopped_by_stale_break = True
                    _ts = datetime.now().strftime("%H:%M:%S")
                    print(
                        f"[{_ts}] [FETCH_PAGE] Facebook: stopping scroll (stale): "
                        f"dom_cards={n} xhr_ids={xhr_n} stale_rounds={stale_rounds} "
                        f"scroll_budget={scroll_budget} round={rounds_done}",
                        flush=True,
                    )
                    break
                stale_rounds = 0

            _nudge_scroll()
            if round_i % 3 == 1:
                try:
                    page.keyboard.press("PageDown")
                except Exception:
                    pass
            elif round_i % 3 == 2:
                try:
                    page.keyboard.press("End")
                except Exception:
                    pass
            page.mouse.wheel(0, 4200)
            page.wait_for_timeout(pause_ms)

        page.wait_for_timeout(800)
        raw_last: list[dict[str, Any]] = page.evaluate(_CARD_SNAPSHOT_JS)
        for c in raw_last:
            lid = str(c.get("id") or "")
            if not lid:
                continue
            if lid not in card_by_id:
                card_by_id[lid] = dict(c)
                ordered_cards.append(card_by_id[lid])
            else:
                cur = card_by_id[lid]
                for img in c.get("image_urls") or []:
                    if img and img not in cur.setdefault("image_urls", []):
                        cur["image_urls"].append(img)

    with sync_playwright() as p:
        if user_data_dir:
            if storage_path:
                _ts = datetime.now().strftime("%H:%M:%S")
                print(
                    f"[{_ts}] [FETCH_PAGE] FACEBOOK_BROWSER_USER_DATA_DIR is set; "
                    "ignoring FACEBOOK_STORAGE_STATE_PATH (profile holds cookies).",
                    flush=True,
                )
            context = p.chromium.launch_persistent_context(
                user_data_dir,
                headless=headless,
                channel=channel,
                viewport={"width": 1400, "height": 900},
                locale="it-IT",
                user_agent=cfg.user_agent,
                args=launch_args,
                ignore_default_args=["--enable-automation"],
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                _attach_and_navigate(page)
                _scroll_phase(page)
            finally:
                context.close()
        else:
            browser = p.chromium.launch(
                headless=headless,
                channel=channel,
                args=launch_args,
                ignore_default_args=["--enable-automation"],
            )
            try:
                ctx_opts: dict[str, Any] = {
                    "user_agent": cfg.user_agent,
                    "locale": "it-IT",
                    "viewport": {"width": 1400, "height": 900},
                }
                if storage_path:
                    ctx_opts["storage_state"] = storage_path
                context = browser.new_context(**ctx_opts)
                try:
                    page = context.new_page()
                    _attach_and_navigate(page)
                    _scroll_phase(page)
                finally:
                    context.close()
            finally:
                browser.close()

    _merge_graphql_into_cards(ordered_cards, graphql_by_id)
    for lid in sorted(graphql_raw_ids):
        if lid in card_by_id:
            continue
        href = f"https://www.facebook.com/marketplace/item/{lid}/"
        c = {
            "id": lid,
            "href": href,
            "title": f"Marketplace item {lid}",
            "price": "",
            "location": "",
            "image_urls": [],
        }
        card_by_id[lid] = c
        ordered_cards.append(c)
    _merge_graphql_into_cards(ordered_cards, graphql_by_id)

    if not ordered_cards:
        _ts = datetime.now().strftime("%H:%M:%S")
        xhr_n = scan_stats["xhr_fetch_bodies"]
        doc_n = scan_stats["document_bodies"]
        rid = len(graphql_raw_ids)
        if xhr_n == 0:
            hint = (
                "No XHR/fetch bodies seen — likely not logged in or the SERP did not load. "
                "Use FACEBOOK_BROWSER_USER_DATA_DIR + login, FACEBOOK_HEADFUL=1, or FACEBOOK_TRACE_RESPONSES=1."
            )
        elif rid == 0:
            hint = (
                "XHR ran but no listing ids were extracted (regex + JSON walk). "
                "Meta may use a new payload shape; set FACEBOOK_TRACE_RESPONSES=1 and/or try headed mode."
            )
        else:
            hint = "Unexpected empty listing list despite raw_ids>0 (please report)."
        print(
            f"[{_ts}] [FETCH_PAGE] Facebook: 0 listings — raw_ids={rid} "
            f"xhr_fetch_bodies={xhr_n} document_bodies={doc_n}. {hint}",
            flush=True,
        )

    def posted_at_for_index(idx: int) -> str | None:
        dt = crawl_epoch - timedelta(minutes=idx)
        return dt.replace(microsecond=0).isoformat()

    effective_url = resolved_nav_url["url"]
    listings = listings_from_cards(
        ordered_cards,
        search_name=search_name,
        search_page_url=effective_url,
        posted_at_for_index=posted_at_for_index,
    )
    reached_end = len(ordered_cards) == 0 or stopped_by_stale_break
    return listings, reached_end, effective_url


def fetch_search_pages(
    cfg: AppConfig,
    search_url: str,
    search_name: str,
    *,
    start_page_index: int = 0,
) -> tuple[list[Listing], bool]:
    page_url = build_fetch_url(
        search_url,
        price_min_eur=cfg.facebook_price_min_eur,
        price_max_eur=cfg.facebook_price_max_eur,
    )
    crawl_epoch = datetime.now(timezone.utc)
    listings, reached_end, resolved_url = _fetch_batch_playwright(
        page_url,
        cfg,
        search_name,
        start_page_index=start_page_index,
        crawl_epoch=crawl_epoch,
    )
    _log_fetch_page(resolved_url, len(listings))
    return listings, reached_end

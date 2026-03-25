from __future__ import annotations

import argparse
import html
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from bikefinder.config import AppConfig, SearchEntry, load_config
from bikefinder.gemini_compare import compare_page_with_gemini
from bikefinder.notify_telegram import send_telegram_message
from bikefinder.models import Listing
from bikefinder.sources import get_listing_source
from bikefinder.sources.base import ListingSource
from bikefinder.storage import Storage
from bikefinder.telegram_command_bot import run_telegram_command_bot


def _score_improved(
    prev_relevance: float,
    new_relevance: float,
) -> bool:
    return new_relevance > prev_relevance + 0.08


def _qualifies_high_confidence_alert(
    score: float, title: str | None, cfg: AppConfig
) -> bool:
    if score > cfg.high_confidence_score_gt:
        return True
    sub = (cfg.high_confidence_title_substring or "").strip().lower()
    if not sub:
        return False
    if sub in (title or "").lower() and score > cfg.high_confidence_title_min_score:
        return True
    return False


def _high_confidence_telegram_creds() -> tuple[str, str] | None:
    token = os.environ.get("TELEGRAM_HIGH_CONFIDENCE_BOT_TOKEN", "").strip()
    cid = os.environ.get("TELEGRAM_HIGH_CONFIDENCE_CHAT_ID", "").strip()
    if token and cid:
        return token, cid
    return None


def _validate_vertex_gemini_config(cfg: AppConfig) -> None:
    if not (cfg.vertex_project_id or "").strip():
        raise SystemExit("VERTEX_PROJECT_ID required (.env or config/config.yaml).")
    if not (cfg.vertex_location or "").strip():
        raise SystemExit("VERTEX_LOCATION required (.env or config/config.yaml).")
    if not (cfg.vertex_gcs_bucket or "").strip():
        raise SystemExit("VERTEX_GCS_BUCKET required (.env or config/config.yaml).")


def _log(stage: str, message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{stage}] {message}", flush=True)


def _parse_cutoff(value: str) -> datetime:
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        dt = datetime.strptime(value, "%d.%m.%Y")
    return dt.replace(tzinfo=timezone.utc)


def _min_listing_day_utc(min_listing_date: str) -> date:
    """First calendar day (UTC) to include; same parsing rules as ``_parse_cutoff``."""
    return _parse_cutoff(min_listing_date).date()


def _posted_before_min_listing_day(posted_dt: datetime, min_day: date) -> bool:
    """True when the listing's UTC calendar day is strictly before ``min_day``."""
    return posted_dt.date() < min_day


def _chunk_by_page(items: list, page_size: int = 30) -> list[list]:
    if page_size <= 0:
        return [items]
    return [items[i : i + page_size] for i in range(0, len(items), page_size)]


def _sort_listings_newest_first(
    page_listings: list[Listing],
    listing_src: ListingSource,
) -> list[Listing]:
    """Newest-first within a SERP chunk so date / existing frontiers match Subito-style behavior."""

    def sort_key(listing: Listing) -> datetime:
        dt = listing_src.parse_posted_at(listing.posted_at)
        if dt is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        return dt

    return sorted(page_listings, key=sort_key, reverse=True)


def _merge_page_groups_for_global_frontier(
    page_groups: list[tuple[str, list[Listing]]],
    listing_src: ListingSource,
    batch_base_url: str,
) -> list[tuple[str, list[Listing]]]:
    """Merge several SERP buckets into one globally newest-first list.

    Wallapop may use the same canonical ``search_page_url`` for every load-more
    chunk (no ``?page=`` on the live SERP). If we applied the date frontier **per
    chunk** instead of merging, an older listing in an early chunk could stop the
    whole run before we process later chunks that still contain listings above
    ``min_listing_date``.
    """
    if len(page_groups) <= 1:
        return page_groups
    merged: list[Listing] = []
    for _, plist in page_groups:
        merged.extend(plist)
    merged = _sort_listings_newest_first(merged, listing_src)
    return [(f"{batch_base_url}#merged_batch", merged)]


def _non_watch_stop_after_batch(
    *,
    reached_end: bool,
    stop_due_to_frontier: bool,
) -> bool:
    """Whether to stop paginating after one fetch batch in non-watch mode."""
    # Newest-only crawl: existing-in-DB frontier or SERP end.
    # Backfill crawl: date floor or SERP end.
    return reached_end or stop_due_to_frontier


def _crawl_date_floor_stops_pagination(
    *, backfill: bool, posted_dt: datetime, min_day: date
) -> bool:
    """In ``crawl()``, the ``min_listing_date`` floor stops paging only in backfill mode.

    Newest-only crawl skips at/below-floor rows without stopping pagination
    (stops instead at first row already in the DB, or SERP end).

    ``min_day`` is inclusive: listings on that UTC calendar day are kept; paging stops
    when the feed reaches a listing from an earlier day (fixes date-only ``posted_at``).
    """
    return bool(backfill and _posted_before_min_listing_day(posted_dt, min_day))


def _expand_append_arg(values: list[str] | None) -> list[str]:
    """Flatten repeated CLI args and comma-separated tokens (strip whitespace, drop empties)."""
    if not values:
        return []
    out: list[str] = []
    for chunk in values:
        for piece in chunk.split(","):
            p = piece.strip()
            if p:
                out.append(p)
    return out


def _apply_search_filters(
    cfg: AppConfig,
    *,
    only_sources: frozenset[str] | None,
    only_search_names: frozenset[str] | None,
) -> AppConfig:
    """Return config with `searches` narrowed, or exit if filters leave nothing."""
    all_searches: list[SearchEntry] = list(cfg.searches)
    filtered: list[SearchEntry] = all_searches

    if only_sources is not None:
        if not only_sources:
            raise SystemExit(
                "--only-source produced no source ids (use e.g. --only-source subito or subito,other)."
            )
        filtered = [s for s in filtered if s.source in only_sources]

    if only_search_names is not None:
        if not only_search_names:
            raise SystemExit(
                "--only-search produced no names (use exact YAML `name`; repeat or comma-separate)."
            )
        filtered = [s for s in filtered if s.name in only_search_names]

    if (only_sources is not None or only_search_names is not None) and not filtered:
        names = ", ".join(repr(s.name) for s in all_searches)
        srcs = ", ".join(sorted({s.source for s in all_searches}))
        extra = ""
        if only_sources is not None:
            for sid in sorted(only_sources):
                if not any(s.source == sid for s in all_searches):
                    extra += (
                        f" No search uses `source: {sid}` — add one under `searches:` "
                        f"(see config/config.example.yaml) and save the file."
                    )
        raise SystemExit(
            "No searches match --only-source / --only-search after filtering. "
            f"In config: names [{names}], sources in use: [{srcs}]."
            f"{extra}"
        )

    if filtered is all_searches:
        return cfg
    return cfg.model_copy(update={"searches": filtered})


def run(
    config_path: Path,
    *,
    dry_run: bool,
    watch: bool,
    interval_minutes: float,
    backfill_cli: bool = False,
    only_sources: frozenset[str] | None = None,
    only_search_names: frozenset[str] | None = None,
) -> int:
    _log("START", f"Loading config from {config_path}")
    load_dotenv()
    if not dry_run:
        if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
            raise SystemExit(
                "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in environment. "
                "Use --dry-run to test without Telegram."
            )
    cfg = load_config(config_path)
    cfg = _apply_search_filters(
        cfg, only_sources=only_sources, only_search_names=only_search_names
    )
    if not cfg.reference_images:
        raise SystemExit("reference_images required in config/config.yaml (Gemini compares these to listings).")
    _validate_vertex_gemini_config(cfg)

    backfill = bool(backfill_cli or cfg.backfill)
    if backfill and watch:
        raise SystemExit("--backfill is for one-shot runs only; do not combine with --watch.")

    newest_only = not backfill
    storage = Storage.from_config(cfg)
    img_client = httpx.Client(timeout=cfg.request_timeout_seconds, follow_redirects=True)
    telegram_sent = 0
    min_day = _min_listing_day_utc(cfg.min_listing_date)
    _log(
        "CONFIG",
        (
            f"matcher=gemini, searches={len(cfg.searches)}, "
            f"pages_per_search={cfg.max_pages_per_search}, dry_run={dry_run}, "
            f"backfill={backfill}, min_listing_date={cfg.min_listing_date}"
        ),
    )
    if _high_confidence_telegram_creds():
        _log(
            "CONFIG",
            "high-confidence Telegram enabled (TELEGRAM_HIGH_CONFIDENCE_BOT_TOKEN + CHAT_ID)",
        )
    else:
        _log(
            "CONFIG",
            "high-confidence Telegram not configured (optional second bot: "
            "TELEGRAM_HIGH_CONFIDENCE_BOT_TOKEN + TELEGRAM_HIGH_CONFIDENCE_CHAT_ID)",
        )

    try:
        run_no = 0
        watch_start_page_by_search: dict[str, int] = {}
        while True:
            run_no += 1
            _log("RUN", f"Starting scan run #{run_no}")
            seen_ids: set[str] = set()
            for si, search in enumerate(cfg.searches):
                hit_cutoff_exit = False
                hit_cutoff_exit_kind = None
                listing_src = get_listing_source(search.source)
                if si > 0 and cfg.delay_seconds > 0:
                    _log("WAIT", f"Sleeping {cfg.delay_seconds:.1f}s before next search")
                    time.sleep(cfg.delay_seconds)
                _log(
                    "FETCH",
                    (
                        f"Search {si + 1}/{len(cfg.searches)} '{search.name}' "
                        f"({listing_src.display_name}) -> fetching listings"
                    ),
                )
                processed_in_search = 0
                start_page_idx = watch_start_page_by_search.get(search.name, 0) if watch else 0
                while True:
                    listings, reached_end = listing_src.fetch_search_pages(
                        cfg,
                        search.url,
                        search.name,
                        start_page_index=start_page_idx,
                    )
                    _log(
                        "FETCH",
                        (
                            f"Search '{search.name}' batch starting at page {start_page_idx + 1} "
                            f"loaded {len(listings)} listings"
                        ),
                    )
                    if not listings:
                        break

                    stop_due_to_frontier = False
                    frontier_kind: str | None = None  # "existing" | "date"
                    has_page_urls = all(getattr(l, "search_page_url", None) for l in listings)
                    if has_page_urls:
                        groups: dict[str, list] = {}
                        for l in listings:
                            key = str(getattr(l, "search_page_url", "") or "")
                            if key not in groups:
                                groups[key] = []
                            groups[key].append(l)
                        page_groups = [
                            (k, _sort_listings_newest_first(groups[k], listing_src))
                            for k in groups
                        ]
                    else:
                        page_groups = [
                            (
                                f"{search.url}#chunk{page_idx}",
                                _sort_listings_newest_first(page_listings, listing_src),
                            )
                            for page_idx, page_listings in enumerate(
                                _chunk_by_page(listings, 30), start=1
                            )
                        ]

                    diag_groups = page_groups
                    proc_groups = _merge_page_groups_for_global_frontier(
                        page_groups, listing_src, search.url
                    )

                    for page_idx, (page_url, page_listings) in enumerate(
                        diag_groups, start=1
                    ):
                        # Log one row per fetched SERP URL.
                        # Log counts/min-max for the actual listings present on this fetched `page_url`.
                        # (Deduplicate only within this page_url group; the crawler's `seen_ids` is used for
                        # candidate processing, not for SERP coverage diagnostics.)
                        # and include items even if pagination processing later stops due to cutoff.
                        # For crawl diagnostics we want the actual SERP coverage for this page URL,
                        # so do NOT subtract globally-seen listings (that would distort min/max posted dates).
                        # Deduplicate only within this page_url group.
                        local_seen_ids: set[str] = set()
                        posted_dt_cache_diag: dict[str, datetime] = {}

                        min_posted_dt: datetime | None = None
                        max_posted_dt: datetime | None = None
                        before_cutoff_count = 0
                        already_in_db_count = 0
                        added_to_db_count = 0

                        for listing in page_listings:
                            if listing.listing_id in local_seen_ids:
                                continue
                            local_seen_ids.add(listing.listing_id)

                            posted_dt = listing_src.parse_posted_at(listing.posted_at)
                            if posted_dt is None:
                                continue

                            posted_dt_cache_diag[listing.listing_id] = posted_dt
                            if min_posted_dt is None or posted_dt < min_posted_dt:
                                min_posted_dt = posted_dt
                            if max_posted_dt is None or posted_dt > max_posted_dt:
                                max_posted_dt = posted_dt

                            # Before min listing day (UTC calendar date).
                            if _posted_before_min_listing_day(posted_dt, min_day):
                                before_cutoff_count += 1
                                continue

                            prev = storage.get(listing.listing_id)
                            if prev is None:
                                added_to_db_count += 1
                            else:
                                already_in_db_count += 1

                        _log(
                            "PAGE_DIAG",
                            (
                                f"page_url={page_url} "
                                f"total_listings={len(page_listings)} "
                                f"deduped={len(local_seen_ids)} "
                                f"valid_posted_at={len(posted_dt_cache_diag)} "
                                f"before_cutoff_count={before_cutoff_count} "
                                f"already_in_db_count={already_in_db_count} "
                                f"added_to_db_count={added_to_db_count} "
                                f"min_posted_at={min_posted_dt} max_posted_at={max_posted_dt}"
                            ),
                        )

                        storage.log_crawl_page(
                            source=search.source,
                            search_name=search.name,
                            page_url=page_url,
                            crawled_at=datetime.now(timezone.utc),
                            already_in_db_count=already_in_db_count,
                            added_to_db_count=added_to_db_count,
                            before_cutoff_count=before_cutoff_count,
                            min_listing_posted_at=min_posted_dt,
                            max_listing_posted_at=max_posted_dt,
                        )

                    for page_idx, (page_url, page_listings) in enumerate(
                        proc_groups, start=1
                    ):
                        posted_dt_cache: dict[str, datetime] = {}
                        prev_cache: dict[str, Any] = {}
                        for listing in page_listings:
                            posted_dt = listing_src.parse_posted_at(listing.posted_at)
                            if posted_dt is None:
                                continue
                            posted_dt_cache[listing.listing_id] = posted_dt
                            if _posted_before_min_listing_day(posted_dt, min_day):
                                continue
                            prev_cache[listing.listing_id] = storage.get(
                                listing.listing_id
                            )

                        candidates: list[tuple] = []
                        for listing in page_listings:
                            if listing.listing_id in seen_ids:
                                continue
                            seen_ids.add(listing.listing_id)

                            lid = listing.listing_id
                            posted_dt = posted_dt_cache.get(lid)
                            if posted_dt is None:
                                posted_dt = listing_src.parse_posted_at(listing.posted_at)
                                if posted_dt is None:
                                    _log(
                                        "SKIP",
                                        f"listing_id={listing.listing_id} missing/invalid posted_at",
                                    )
                                    continue

                            if _posted_before_min_listing_day(posted_dt, min_day):
                                _log(
                                    "CUTOFF",
                                    (
                                        "Date frontier reached (listing day before min_listing_date); "
                                        f"stopping this search (listing_id={listing.listing_id}, "
                                        f"posted_at={listing.posted_at})"
                                    ),
                                )
                                stop_due_to_frontier = True
                                frontier_kind = "date"
                                hit_cutoff_exit = True
                                hit_cutoff_exit_kind = "date"
                                break

                            # Newest-only mode stops at the first already-stored and already-scored listing.
                            # Fresh-fill mode keeps going regardless of DB presence.
                            prev = (
                                prev_cache.get(lid) if lid in prev_cache else storage.get(lid)
                            )
                            if (
                                newest_only
                                and prev is not None
                                and prev.image_relevance >= 0.0
                            ):
                                _log(
                                    "CUTOFF",
                                    (
                                        "Existing listing frontier reached; stopping this search "
                                        f"(listing_id={listing.listing_id}, posted_at={listing.posted_at})"
                                    ),
                                )
                                stop_due_to_frontier = True
                                frontier_kind = "existing"
                                hit_cutoff_exit = True
                                hit_cutoff_exit_kind = "existing"
                                break

                            if prev is not None and prev.image_relevance >= 0.0:
                                _log(
                                    "SKIP",
                                    f"listing_id={listing.listing_id} already compared (image_relevance>=0)",
                                )
                                continue

                            candidates.append((listing, prev))

                        gemini_page_scores: dict[str, dict] = {}
                        if candidates:
                            _log(
                                "GEMINI",
                                (
                                    f"{search.name} start_page {start_page_idx + 1} page {page_idx}: "
                                    f"sending one prompt for {len(candidates)} listings"
                                ),
                            )
                            gemini_page_scores = compare_page_with_gemini(
                                cfg,
                                [listing for listing, _prev in candidates],
                                img_client,
                            )

                        for listing, prev in candidates:
                            processed_in_search += 1
                            is_new = prev is None
                            prev_relevance = prev.image_relevance if prev else 0.0

                            gemini_result = gemini_page_scores.get(listing.listing_id, {})
                            gemini_score = float(gemini_result.get("relevance_score", 0.0) or 0.0)
                            gemini_reason = str(gemini_result.get("reason") or "").strip()
                            relevance = gemini_score
                            qualifies = gemini_score >= cfg.gemini_threshold

                            storage.upsert(
                                listing.listing_id,
                                listing.url,
                                listing.title,
                                listing.posted_at,
                                relevance,
                                gemini_reason=gemini_reason or None,
                                price=listing.price or None,
                                image_urls=listing.image_urls,
                                search_name=listing.search_name or "",
                                location=listing.location,
                            )

                            dedup = is_new or _score_improved(
                                prev_relevance, relevance
                            )
                            if not dedup:
                                skip_msg = (
                                    f"listing_id={listing.listing_id} "
                                    "skip notify: not new and score not improved by >0.08"
                                )
                                if gemini_reason:
                                    rshort = gemini_reason.replace("\n", " ")
                                    if len(rshort) > 120:
                                        rshort = rshort[:119] + "…"
                                    skip_msg = f"{skip_msg} | {rshort}"
                                _log("SKIP", skip_msg)
                                continue

                            send_main = qualifies
                            qualifies_high = _qualifies_high_confidence_alert(
                                gemini_score, listing.title, cfg
                            )
                            high_creds = _high_confidence_telegram_creds()
                            will_send_high = bool(
                                qualifies_high and high_creds is not None
                            )
                            if not send_main and not will_send_high:
                                skip_msg = (
                                    f"listing_id={listing.listing_id} "
                                    f"score={gemini_score:.2f} < threshold={cfg.gemini_threshold:.2f}"
                                )
                                if qualifies_high and high_creds is None:
                                    skip_msg = (
                                        f"{skip_msg} | high-confidence match but "
                                        "TELEGRAM_HIGH_CONFIDENCE_* not set"
                                    )
                                elif not qualifies_high:
                                    skip_msg = (
                                        f"{skip_msg} | not high-confidence "
                                        f"(score>{cfg.high_confidence_score_gt:.2f} "
                                        f"or ({cfg.high_confidence_title_substring!r} in title "
                                        f"and score>{cfg.high_confidence_title_min_score:.2f}))"
                                    )
                                if gemini_reason:
                                    rshort = gemini_reason.replace("\n", " ")
                                    if len(rshort) > 120:
                                        rshort = rshort[:119] + "…"
                                    skip_msg = f"{skip_msg} | {rshort}"
                                _log("SKIP", skip_msg)
                                continue

                            def _e(s: str | None) -> str:
                                return html.escape(s or "", quote=False)

                            reason_1l = " ".join(gemini_reason.split()) if gemini_reason else ""
                            lines = [
                                f"Search: {_e(listing.search_name)}",
                                f"Price: {_e(listing.price or '?')}",
                                f"Where: {_e(listing.location or '?')}",
                                f"Posted at: {_e(listing.posted_at or '?')}",
                                f"<b>Match score: {gemini_score:.2f}</b>",
                            ]
                            if reason_1l:
                                lines.append(f"Gemini note: {_e(reason_1l)}")
                            href = html.escape((listing.url or "").strip(), quote=True)
                            if href:
                                lines.append(f'<a href="{href}">Open listing</a>')
                            msg = "\n".join(lines)
                            msg_high = (
                                "<b>Sure match (high confidence)</b>\n" + msg
                            )

                            try:
                                sent_any = False
                                if send_main:
                                    send_telegram_message(
                                        msg,
                                        dry_run=dry_run,
                                        parse_mode="HTML",
                                        dry_run_label="telegram main",
                                    )
                                    telegram_sent += 1
                                    sent_any = True
                                    _log(
                                        "ALERT",
                                        (
                                            f"Sent alert #{telegram_sent} (main) "
                                            f"for listing_id={listing.listing_id}"
                                        ),
                                    )
                                if qualifies_high and high_creds:
                                    send_telegram_message(
                                        msg_high,
                                        dry_run=dry_run,
                                        parse_mode="HTML",
                                        bot_token=high_creds[0],
                                        chat_id=high_creds[1],
                                        dry_run_label="telegram high-confidence",
                                    )
                                    telegram_sent += 1
                                    sent_any = True
                                    _log(
                                        "ALERT",
                                        (
                                            f"Sent alert #{telegram_sent} (high-confidence) "
                                            f"for listing_id={listing.listing_id}"
                                        ),
                                    )
                                if sent_any and not dry_run:
                                    storage.mark_notified(listing.listing_id)
                            except RuntimeError as e:
                                if not dry_run:
                                    raise
                                print(f"Telegram skipped: {e}")

                        if stop_due_to_frontier:
                            break
                    if watch:
                        if reached_end or stop_due_to_frontier:
                            watch_start_page_by_search[search.name] = 0
                            if reached_end:
                                _log(
                                    "FETCH",
                                    f"Search '{search.name}' reached end; next cycle restarts from newest batch",
                                )
                            else:
                                if frontier_kind == "existing":
                                    _log(
                                        "FETCH",
                                        f"Search '{search.name}' hit existing listing frontier",
                                    )
                                else:
                                    _log(
                                        "FETCH",
                                        f"Search '{search.name}' hit date cutoff frontier",
                                    )
                        else:
                            watch_start_page_by_search[search.name] = (
                                start_page_idx + cfg.max_pages_per_search
                            )
                        break

                    if _non_watch_stop_after_batch(
                        reached_end=reached_end,
                        stop_due_to_frontier=stop_due_to_frontier,
                    ):
                        break

                    start_page_idx += cfg.max_pages_per_search
                    _log(
                        "FETCH",
                        (
                            f"Search '{search.name}' next batch had no new listings yet; "
                            "loading next batch"
                        ),
                    )
                _log(
                    "SEARCH_DONE",
                    f"Search '{search.name}' processed {processed_in_search} NEW listings after cutoff",
                )

            if not watch:
                break
            _log("WATCH", "Run complete. Continuing immediately (no sleep)")
    finally:
        img_client.close()
        storage.close()

    _log("DONE", f"Telegram messages sent (or printed): {telegram_sent}")
    return 0


def crawl(
    config_path: Path,
    *,
    dry_run: bool,
    only_sources: frozenset[str] | None = None,
    only_search_names: frozenset[str] | None = None,
    backfill_cli: bool = False,
    today_cli: bool = False,
) -> int:
    """
    Crawl listings and fill/update Postgres without calling Gemini.

    Rows inserted by this process are marked as "needs scoring" via:
    - `image_relevance = -1.0` (when images exist but are not scored yet)
    - `image_relevance = 0.0` (when there are no images, so Gemini must never score)
    """
    _log("START", f"Loading config from {config_path}")
    load_dotenv()
    cfg = load_config(config_path)
    cfg = _apply_search_filters(
        cfg, only_sources=only_sources, only_search_names=only_search_names
    )

    if today_cli:
        # UTC floor at midnight (same format accepted by _parse_cutoff()).
        cfg.min_listing_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        backfill_cli = True  # Explicitly match the requested behavior.

    storage = Storage.from_config(cfg)
    min_day = _min_listing_day_utc(cfg.min_listing_date)
    backfill = bool(backfill_cli or cfg.backfill)
    newest_only = not backfill
    _log(
        "CONFIG",
        (
            f"crawl: newest_only={newest_only} "
            f"(backfill={backfill}), min_listing_date={cfg.min_listing_date}"
        ),
    )
    if today_cli:
        srcs = sorted({s.source for s in cfg.searches})
        _log(
            "CONFIG",
            f"crawl --today: {len(cfg.searches)} search(es) across source(s) {srcs}",
        )

    try:
        for si, search in enumerate(cfg.searches):
            listing_src = get_listing_source(search.source)
            if si > 0 and cfg.delay_seconds > 0:
                _log("WAIT", f"Sleeping {cfg.delay_seconds:.1f}s before next search")
                time.sleep(cfg.delay_seconds)

            _log(
                "FETCH",
                (
                    f"Search '{search.name}' ({listing_src.display_name}) "
                    "-> crawling listings and filling DB"
                ),
            )

            seen_ids: set[str] = set()
            start_page_idx = 0

            while True:
                listings, reached_end = listing_src.fetch_search_pages(
                    cfg,
                    search.url,
                    search.name,
                    start_page_index=start_page_idx,
                )
                _log(
                    "FETCH",
                    f"Search '{search.name}' batch starting at page {start_page_idx + 1}: loaded {len(listings)} listings",
                )
                if not listings:
                    break

                stop_due_to_frontier = False
                hit_cutoff_exit = False
                frontier_kind: str | None = None  # "date"

                # Keep page grouping as in run() so crawl_page_logs are meaningful.
                has_page_urls = all(
                    getattr(l, "search_page_url", None) for l in listings
                )
                if has_page_urls:
                    groups: dict[str, list] = {}
                    for l in listings:
                        key = str(getattr(l, "search_page_url", "") or "")
                        if key not in groups:
                            groups[key] = []
                        groups[key].append(l)
                    page_groups = [
                        (k, _sort_listings_newest_first(groups[k], listing_src))
                        for k in groups
                    ]
                else:
                    page_groups = [
                        (
                            f"{search.url}#chunk{page_idx}",
                            _sort_listings_newest_first(page_listings, listing_src),
                        )
                        for page_idx, page_listings in enumerate(
                            _chunk_by_page(listings, 30), start=1
                        )
                    ]

                diag_groups = page_groups
                proc_groups = _merge_page_groups_for_global_frontier(
                    page_groups, listing_src, search.url
                )

                for page_idx, (page_url, page_listings) in enumerate(
                    diag_groups, start=1
                ):
                    # Per-SERP diagnostics only (Wallapop: one row per ?page=N).
                    local_seen_ids: set[str] = set()
                    posted_dt_cache_diag: dict[str, datetime] = {}

                    min_posted_dt: datetime | None = None
                    max_posted_dt: datetime | None = None
                    before_cutoff_count = 0
                    already_in_db_count = 0
                    added_to_db_count = 0

                    for listing in page_listings:
                        if listing.listing_id in local_seen_ids:
                            continue
                        local_seen_ids.add(listing.listing_id)

                        posted_dt = listing_src.parse_posted_at(listing.posted_at)
                        if posted_dt is None:
                            continue

                        posted_dt_cache_diag[listing.listing_id] = posted_dt
                        if min_posted_dt is None or posted_dt < min_posted_dt:
                            min_posted_dt = posted_dt
                        if max_posted_dt is None or posted_dt > max_posted_dt:
                            max_posted_dt = posted_dt

                        if _posted_before_min_listing_day(posted_dt, min_day):
                            before_cutoff_count += 1
                            continue

                        prev = storage.get(listing.listing_id)
                        if prev is None:
                            added_to_db_count += 1
                        else:
                            already_in_db_count += 1

                    storage.log_crawl_page(
                        source=search.source,
                        search_name=search.name,
                        page_url=page_url,
                        crawled_at=datetime.now(timezone.utc),
                        already_in_db_count=already_in_db_count,
                        added_to_db_count=added_to_db_count,
                        before_cutoff_count=before_cutoff_count,
                        min_listing_posted_at=min_posted_dt,
                        max_listing_posted_at=max_posted_dt,
                    )

                for page_idx, (page_url, page_listings) in enumerate(
                    proc_groups, start=1
                ):
                    posted_dt_cache: dict[str, datetime] = {}
                    prev_cache: dict[str, Any] = {}
                    for listing in page_listings:
                        posted_dt = listing_src.parse_posted_at(listing.posted_at)
                        if posted_dt is None:
                            continue
                        posted_dt_cache[listing.listing_id] = posted_dt
                        if _posted_before_min_listing_day(posted_dt, min_day):
                            continue
                        prev_cache[listing.listing_id] = storage.get(
                            listing.listing_id
                        )

                    # Upserts + frontier: global newest-first when proc_groups was merged.
                    for listing in page_listings:
                        if listing.listing_id in seen_ids:
                            continue
                        seen_ids.add(listing.listing_id)

                        lid = listing.listing_id
                        posted_dt = posted_dt_cache.get(lid)
                        if posted_dt is None:
                            posted_dt = listing_src.parse_posted_at(
                                listing.posted_at
                            )
                            if posted_dt is None:
                                continue

                        if _posted_before_min_listing_day(posted_dt, min_day):
                            if _crawl_date_floor_stops_pagination(
                                backfill=backfill,
                                posted_dt=posted_dt,
                                min_day=min_day,
                            ):
                                _log(
                                    "CUTOFF",
                                    (
                                        "Date frontier reached; stopping this search "
                                        f"(listing_id={listing.listing_id}, posted_at={listing.posted_at})"
                                    ),
                                )
                                stop_due_to_frontier = True
                                frontier_kind = "date"
                                hit_cutoff_exit = True
                                break
                            continue

                        # Newest-only crawl stops when we hit the first listing already present in DB.
                        prev = prev_cache.get(lid)
                        if newest_only and prev is not None:
                            _log(
                                "CUTOFF",
                                (
                                    "Existing listing frontier reached; stopping this search "
                                    f"(listing_id={listing.listing_id}, posted_at={listing.posted_at})"
                                ),
                            )
                            stop_due_to_frontier = True
                            frontier_kind = "existing"
                            hit_cutoff_exit = True
                            break

                        if prev is None:
                            # If there are no listing images, mark as non-relevant so:
                            # - score() query won't pick it up again
                            # - newest-only pagination frontier can stop correctly
                            image_relevance = -1.0 if listing.image_urls else 0.0
                            gemini_reason = None
                        else:
                            image_relevance = prev.image_relevance
                            gemini_reason = prev.gemini_reason
                            # If we have score but no response yet, mark as needing Gemini.
                            # Only do this for listings that were never notified to avoid duplicates.
                            if (
                                image_relevance >= 0.0
                                and gemini_reason is None
                                and prev.notified == 0
                            ):
                                # Never force re-scoring if the listing has no images.
                                # (Gemini prompt candidates are built from successfully downloaded images.)
                                if listing.image_urls:
                                    image_relevance = -1.0
                                    gemini_reason = None
                                else:
                                    image_relevance = 0.0
                                    gemini_reason = None

                            # Also handle legacy rows that were inserted as "needs scoring"
                            # even though the listing had no images.
                            if (not listing.image_urls) and image_relevance < 0.0:
                                image_relevance = 0.0
                                gemini_reason = None

                        if not dry_run:
                            storage.upsert(
                                listing.listing_id,
                                listing.url,
                                listing.title,
                                listing.posted_at,
                                image_relevance,
                                gemini_reason=gemini_reason,
                                price=listing.price or None,
                                image_urls=listing.image_urls,
                                search_name=listing.search_name or "",
                                location=listing.location,
                            )

                if stop_due_to_frontier:
                    break

                if reached_end:
                    break

                start_page_idx += cfg.max_pages_per_search

            _log("SEARCH_DONE", f"Search '{search.name}' crawling complete")
    finally:
        storage.close()

    _log("DONE", "Crawl complete")
    return 0


def score(
    config_path: Path,
    *,
    dry_run: bool,
    only_sources: frozenset[str] | None = None,
    only_search_names: frozenset[str] | None = None,
) -> int:
    """
    Run Gemini scoring only for listings marked as needing a score:
    ``image_relevance = -1`` (and non-empty ``image_urls``).

    Uses the same config / ``--only-source`` / ``--only-search`` narrowing as
    ``crawl``; only rows whose ``listing_id`` prefix and ``search_name`` match
    the current search list are scored (Subito, Wallapop, etc.).
    """
    _log("START", f"Loading config from {config_path}")
    load_dotenv()

    if not dry_run:
        if (
            not os.environ.get("TELEGRAM_BOT_TOKEN")
            or not os.environ.get("TELEGRAM_CHAT_ID")
        ):
            raise SystemExit(
                "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in environment. "
                "Use --dry-run to test without Telegram."
            )

    cfg = load_config(config_path)
    cfg = _apply_search_filters(
        cfg, only_sources=only_sources, only_search_names=only_search_names
    )
    if not cfg.reference_images:
        raise SystemExit(
            "reference_images required in config/config.yaml (Gemini compares these to listings)."
        )
    _validate_vertex_gemini_config(cfg)

    storage = Storage.from_config(cfg)
    img_client = httpx.Client(
        timeout=cfg.request_timeout_seconds, follow_redirects=True
    )
    telegram_sent = 0

    _log(
        "CONFIG",
        (
            f"matcher=gemini, searches={len(cfg.searches)}, "
            f"unscored=image_relevance=-1 with images, "
            f"threshold={cfg.gemini_threshold:.2f}"
        ),
    )
    if _high_confidence_telegram_creds():
        _log(
            "CONFIG",
            "high-confidence Telegram enabled (TELEGRAM_HIGH_CONFIDENCE_BOT_TOKEN + CHAT_ID)",
        )
    else:
        _log(
            "CONFIG",
            "high-confidence Telegram not configured (optional second bot: "
            "TELEGRAM_HIGH_CONFIDENCE_BOT_TOKEN + TELEGRAM_HIGH_CONFIDENCE_CHAT_ID)",
        )

    source_scope = frozenset(s.source.lower() for s in cfg.searches)
    name_scope = frozenset(s.name for s in cfg.searches)

    def _e(s: str | None) -> str:
        return html.escape(s or "", quote=False)

    try:
        while True:
            batch = storage.fetch_unscored_listings_batch(
                limit=30,
                source_ids=source_scope,
                search_names=name_scope,
            )
            if not batch:
                break

            sl_by_id = {sl.listing_id: sl for sl in batch}
            page_listings: list[Listing] = []
            for sl in batch:
                page_listings.append(
                    Listing(
                        listing_id=sl.listing_id,
                        url=sl.url,
                        title=sl.title,
                        body="",
                        posted_at=sl.listing_posted_at,
                        price=sl.price,
                        location=sl.location,
                        image_urls=sl.image_urls,
                        search_name=sl.search_name or "",
                        source=sl.listing_id.split(":", 1)[0]
                        if ":" in sl.listing_id
                        else "",
                    )
                )

            _log("GEMINI", f"Scoring {len(page_listings)} listings via Gemini")
            t_cmp0 = time.monotonic()
            gemini_page_scores = compare_page_with_gemini(
                cfg, page_listings, img_client
            )
            t_cmp_s = time.monotonic() - t_cmp0
            _log("GEMINI_TIMING", f"batch_score_time_s={t_cmp_s:.2f}")

            for sl in batch:
                prev_relevance = sl.image_relevance
                gemini_result = gemini_page_scores.get(sl.listing_id, {})
                gemini_score = float(
                    gemini_result.get("relevance_score", 0.0) or 0.0
                )
                gemini_reason = str(gemini_result.get("reason") or "").strip()
                relevance = gemini_score
                qualifies = gemini_score >= cfg.gemini_threshold

                storage.upsert(
                    sl.listing_id,
                    sl.url,
                    sl.title,
                    sl.listing_posted_at,
                    relevance,
                    gemini_reason=gemini_reason or None,
                    price=sl.price,
                    image_urls=sl.image_urls,
                    search_name=sl.search_name or "",
                    location=sl.location,
                )

                is_new = False
                dedup = is_new or _score_improved(prev_relevance, relevance)
                if not dedup:
                    continue

                send_main = qualifies
                qualifies_high = _qualifies_high_confidence_alert(
                    gemini_score, sl.title, cfg
                )
                high_creds = _high_confidence_telegram_creds()
                will_send_high = bool(
                    qualifies_high and high_creds is not None
                )
                if not send_main and not will_send_high:
                    continue

                reason_1l = (
                    " ".join(gemini_reason.split()) if gemini_reason else ""
                )
                lines = [
                    f"Search: {_e(sl.search_name)}",
                    f"Price: {_e(sl.price or '?')}",
                    f"Where: {_e(sl.location or '?')}",
                    f"Posted at: {_e(sl.listing_posted_at or '?')}",
                    f"<b>Match score: {gemini_score:.2f}</b>",
                ]
                if reason_1l:
                    lines.append(f"Gemini note: {_e(reason_1l)}")
                href = html.escape((sl.url or "").strip(), quote=True)
                if href:
                    lines.append(f'<a href="{href}">Open listing</a>')
                msg = "\n".join(lines)
                msg_high = "<b>Sure match (high confidence)</b>\n" + msg

                try:
                    sent_any = False
                    if send_main:
                        send_telegram_message(
                            msg,
                            dry_run=dry_run,
                            parse_mode="HTML",
                            dry_run_label="telegram main",
                        )
                        telegram_sent += 1
                        sent_any = True
                        _log(
                            "ALERT",
                            (
                                f"Sent alert #{telegram_sent} (main) "
                                f"for listing_id={sl.listing_id}"
                            ),
                        )
                    if qualifies_high and high_creds:
                        send_telegram_message(
                            msg_high,
                            dry_run=dry_run,
                            parse_mode="HTML",
                            bot_token=high_creds[0],
                            chat_id=high_creds[1],
                            dry_run_label="telegram high-confidence",
                        )
                        telegram_sent += 1
                        sent_any = True
                        _log(
                            "ALERT",
                            (
                                f"Sent alert #{telegram_sent} (high-confidence) "
                                f"for listing_id={sl.listing_id}"
                            ),
                        )
                    if sent_any and not dry_run:
                        storage.mark_notified(sl.listing_id)
                except RuntimeError as e:
                    if not dry_run:
                        raise
                    print(f"Telegram skipped: {e}")

    finally:
        img_client.close()
        storage.close()

    _log("DONE", f"Telegram messages sent (or printed): {telegram_sent}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor classifieds (Subito.it and pluggable sources) for bike listings"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="Fetch searches, match with Gemini, notify")
    p_run.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to config YAML",
    )
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matches to stdout; do not send Telegram",
    )
    p_run.add_argument(
        "--watch",
        action="store_true",
        help="Run continuously and poll for new listings",
    )
    p_run.add_argument(
        "--backfill",
        action="store_true",
        help=(
            "Non-watch only: keep paging until SERP end or min_listing_date frontier "
            "(config key `backfill: true` also enables)"
        ),
    )
    p_run.add_argument(
        "--interval-minutes",
        type=float,
        default=30.0,
        help="Polling interval in minutes when --watch is enabled",
    )
    p_run.add_argument(
        "--only-source",
        dest="only_source",
        action="append",
        default=None,
        metavar="SOURCE",
        help=(
            "Run only searches with this marketplace source id (repeat or comma-separated); "
            "useful for one Windows scheduled task per site."
        ),
    )
    p_run.add_argument(
        "--only-search",
        dest="only_search",
        action="append",
        default=None,
        metavar="NAME",
        help="Run only searches whose YAML `name` matches exactly (repeat or comma-separated).",
    )

    p_crawl = sub.add_parser(
        "crawl",
        help="Crawl listings and fill Postgres (no Gemini, no Telegram).",
    )
    p_crawl.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to config YAML",
    )
    p_crawl.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not upsert into Postgres (still crawls and logs).",
    )
    p_crawl.add_argument(
        "--backfill",
        action="store_true",
        help=(
            "Crawl through pages until min_listing_date, ignoring whether listings already exist in the DB. "
            "ORs with config key `backfill: true`."
        ),
    )
    p_crawl.add_argument(
        "--today",
        action="store_true",
        help=(
            "One-shot: set min_listing_date to today's UTC date (YYYY-MM-DD) and enable backfill "
            "so it crawls until the date frontier."
        ),
    )
    p_crawl.add_argument(
        "--only-source",
        dest="only_source",
        action="append",
        default=None,
        metavar="SOURCE",
        help=(
            "Run only searches with this marketplace source id (repeat or comma-separated); "
            "useful for one Windows scheduled task per site."
        ),
    )
    p_crawl.add_argument(
        "--only-search",
        dest="only_search",
        action="append",
        default=None,
        metavar="NAME",
        help="Run only searches whose YAML `name` matches exactly (repeat or comma-separated).",
    )

    p_score = sub.add_parser(
        "score",
        help=(
            "Run Gemini on listings with image_relevance=-1 and images "
            "(same config narrowing as crawl; use after crawl per source)."
        ),
    )
    p_score.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to config YAML",
    )
    p_score.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not send Telegram; still prints/scoring to stdout where supported.",
    )
    p_score.add_argument(
        "--only-source",
        dest="only_source",
        action="append",
        default=None,
        metavar="SOURCE",
        help=(
            "Score only rows whose listing_id matches this source prefix "
            "(repeat or comma-separated); narrows config searches like crawl."
        ),
    )
    p_score.add_argument(
        "--only-search",
        dest="only_search",
        action="append",
        default=None,
        metavar="NAME",
        help="Run only searches whose YAML `name` matches exactly (repeat or comma-separated).",
    )

    p_tg = sub.add_parser(
        "telegram-bot",
        help=(
            "Long-poll Telegram: reply to /stats with per-source scrape and scoring counts "
            "(needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)."
        ),
    )
    p_tg.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to config YAML (for database_url)",
    )
    p_tg.add_argument(
        "--stats-hours",
        type=float,
        default=1.0,
        help="Default UTC lookback for /stats when you do not pass hours (default: 1).",
    )

    args = parser.parse_args()
    if args.cmd == "run":
        if not args.config.is_file():
            raise SystemExit(f"Config not found: {args.config}")
        src_tokens = _expand_append_arg(args.only_source)
        search_tokens = _expand_append_arg(args.only_search)
        only_sources = frozenset(t.lower() for t in src_tokens) if src_tokens else None
        only_search_names = frozenset(search_tokens) if search_tokens else None
        raise SystemExit(
            run(
                args.config,
                dry_run=args.dry_run,
                watch=args.watch,
                interval_minutes=args.interval_minutes,
                backfill_cli=args.backfill,
                only_sources=only_sources,
                only_search_names=only_search_names,
            )
        )

    if args.cmd == "crawl":
        if not args.config.is_file():
            raise SystemExit(f"Config not found: {args.config}")
        src_tokens = _expand_append_arg(args.only_source)
        search_tokens = _expand_append_arg(args.only_search)
        only_sources = frozenset(t.lower() for t in src_tokens) if src_tokens else None
        only_search_names = frozenset(search_tokens) if search_tokens else None
        raise SystemExit(
            crawl(
                args.config,
                dry_run=args.dry_run,
                backfill_cli=args.backfill,
                today_cli=args.today,
                only_sources=only_sources,
                only_search_names=only_search_names,
            )
        )

    if args.cmd == "score":
        if not args.config.is_file():
            raise SystemExit(f"Config not found: {args.config}")
        src_tokens = _expand_append_arg(args.only_source)
        search_tokens = _expand_append_arg(args.only_search)
        only_sources = frozenset(t.lower() for t in src_tokens) if src_tokens else None
        only_search_names = frozenset(search_tokens) if search_tokens else None
        raise SystemExit(
            score(
                args.config,
                dry_run=args.dry_run,
                only_sources=only_sources,
                only_search_names=only_search_names,
            )
        )

    if args.cmd == "telegram-bot":
        if not args.config.is_file():
            raise SystemExit(f"Config not found: {args.config}")
        if args.stats_hours <= 0:
            raise SystemExit("--stats-hours must be positive")
        load_dotenv()
        cfg = load_config(args.config)
        raise SystemExit(
            run_telegram_command_bot(cfg, default_stats_hours=args.stats_hours)
        )


if __name__ == "__main__":
    main()

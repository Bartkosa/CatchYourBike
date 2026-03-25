"""Tests for non-watch pagination stop logic."""

from datetime import date, datetime, timezone

from bikefinder.cli import (
    _crawl_date_floor_stops_pagination,
    _non_watch_stop_after_batch,
)


def test_non_watch_stop_only_on_end_or_frontier() -> None:
    assert _non_watch_stop_after_batch(
        reached_end=True,
        stop_due_to_frontier=False,
    )
    assert _non_watch_stop_after_batch(
        reached_end=False,
        stop_due_to_frontier=True,
    )
    assert not _non_watch_stop_after_batch(
        reached_end=False,
        stop_due_to_frontier=False,
    )


def test_crawl_date_floor_stops_pagination_only_in_backfill() -> None:
    min_day = date(2026, 3, 19)
    older = datetime(2026, 3, 17, 12, 0, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 3, 20, 0, 0, 0, tzinfo=timezone.utc)

    assert _crawl_date_floor_stops_pagination(
        backfill=True, posted_dt=older, min_day=min_day
    )
    assert not _crawl_date_floor_stops_pagination(
        backfill=False, posted_dt=older, min_day=min_day
    )
    assert not _crawl_date_floor_stops_pagination(
        backfill=True, posted_dt=newer, min_day=min_day
    )
    assert not _crawl_date_floor_stops_pagination(
        backfill=False, posted_dt=newer, min_day=min_day
    )


def test_crawl_date_floor_includes_min_listing_calendar_day() -> None:
    """Date-only / midnight timestamps on min_listing_date must not stop the crawl."""
    min_day = date(2026, 3, 19)
    on_min_day_midnight = datetime(2026, 3, 19, 0, 0, 0, tzinfo=timezone.utc)
    assert not _crawl_date_floor_stops_pagination(
        backfill=True, posted_dt=on_min_day_midnight, min_day=min_day
    )

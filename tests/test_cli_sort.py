"""Tests for SERP ordering helpers used by crawl/run."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from bikefinder.cli import (
    _merge_page_groups_for_global_frontier,
    _sort_listings_newest_first,
)
from bikefinder.models import Listing


def test_sort_listings_newest_first_orders_by_parsed_posted_at() -> None:
    src = MagicMock()

    def parse_posted_at(value: str | None) -> datetime | None:
        if value == "new":
            return datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc)
        if value == "old":
            return datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)
        return None

    src.parse_posted_at = parse_posted_at

    listings = [
        Listing(
            listing_id="1",
            url="u",
            title="t",
            body="",
            posted_at="old",
            price=None,
            location=None,
            image_urls=[],
            search_name="s",
        ),
        Listing(
            listing_id="2",
            url="u",
            title="t",
            body="",
            posted_at="new",
            price=None,
            location=None,
            image_urls=[],
            search_name="s",
        ),
    ]
    out = _sort_listings_newest_first(listings, src)
    assert [x.listing_id for x in out] == ["2", "1"]


def test_merge_page_groups_orders_newest_first_globally() -> None:
    src = MagicMock()

    def parse_posted_at(value: str | None) -> datetime | None:
        if value == "a":
            return datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc)
        if value == "b":
            return datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc)
        if value == "c":
            return datetime(2026, 3, 20, 18, 0, 0, tzinfo=timezone.utc)
        return None

    src.parse_posted_at = parse_posted_at

    g1 = [
        Listing("1", "u", "t", "", "b", None, None, [], "s"),
        Listing("2", "u", "t", "", "a", None, None, [], "s"),
    ]
    g2 = [Listing("3", "u", "t", "", "c", None, None, [], "s")]
    merged = _merge_page_groups_for_global_frontier(
        [("https://x?page=1", g1), ("https://x?page=2", g2)],
        src,
        "https://x/search",
    )
    assert len(merged) == 1
    assert merged[0][0].endswith("#merged_batch")
    assert [x.listing_id for x in merged[0][1]] == ["3", "2", "1"]


def test_merge_page_groups_single_bucket_unchanged() -> None:
    src = MagicMock()
    src.parse_posted_at = lambda v: datetime(2026, 1, 1, tzinfo=timezone.utc)
    one = [Listing("1", "u", "t", "", "x", None, None, [], "s")]
    out = _merge_page_groups_for_global_frontier(
        [("https://x", one)], src, "https://x"
    )
    assert out == [("https://x", one)]

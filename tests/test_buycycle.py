from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bikefinder.models import Listing
from bikefinder.sources.buycycle import BuycycleSource
from bikefinder.sources.buycycle.serp_dom import listing_from_snapshot, listings_from_snapshots
from bikefinder.sources.buycycle.urls import assert_buycycle_search_url, build_serp_url


def test_assert_buycycle_search_url_ok():
    assert_buycycle_search_url(
        "https://buycycle.com/it-it/shop/main-types/bikes/sort-by/new"
    )


def test_assert_buycycle_search_url_rejects_non_shop():
    with pytest.raises(ValueError, match="/shop/"):
        assert_buycycle_search_url("https://buycycle.com/it-it/page/about")


def test_build_serp_url_inserts_price_before_sort_by():
    u = build_serp_url(
        "https://buycycle.com/it-it/shop/main-types/bikes/sort-by/new",
        price_min_eur=150,
        price_max_eur=1200,
        page_1based=1,
    )
    assert (
        u
        == "https://buycycle.com/it-it/shop/main-types/bikes/min-price/150/max-price/1200/sort-by/new"
    )


def test_build_serp_url_strips_trailing_page_for_page_one():
    u = build_serp_url(
        "https://buycycle.com/it-it/shop/main-types/bikes/sort-by/new/page/5",
        price_min_eur=150,
        price_max_eur=1200,
        page_1based=1,
    )
    assert "/page/" not in u
    assert u.endswith("/sort-by/new")


def test_build_serp_url_replaces_existing_price_and_adds_page():
    u = build_serp_url(
        "https://buycycle.com/it-it/shop/main-types/bikes/min-price/1/max-price/9/sort-by/new",
        price_min_eur=150,
        price_max_eur=1200,
        page_1based=3,
    )
    assert "min-price/150" in u
    assert "max-price/1200" in u
    assert u.rstrip("/").endswith("/sort-by/new/page/3")
    assert "page=3" not in u


def test_listing_from_snapshot():
    L = listing_from_snapshot(
        {
            "id": "1985562",
            "name": "Big.nine SRAM GX Eagle",
            "price": "900",
            "href": "/it-it/product/bignine-sram-gx-eagle-64369",
            "src": "https://cdn.example.com/x.webp",
            "text": "Merida\nBig.nine\n€ 914",
        },
        search_name="s",
        search_page_url="https://buycycle.com/...",
        posted_at="2026-03-21T12:00:00+00:00",
    )
    assert L is not None
    assert L.listing_id == "1985562"
    assert L.url == "https://buycycle.com/it-it/product/bignine-sram-gx-eagle-64369"
    assert L.title == "Big.nine SRAM GX Eagle"
    assert L.price == "€ 900"
    assert L.posted_at == "2026-03-21T12:00:00+00:00"


def test_listings_from_snapshots_posted_order():
    rows = [
        {"id": "1", "name": "A", "price": "1", "href": "/it-it/product/a", "src": "", "text": ""},
        {"id": "2", "name": "B", "price": "2", "href": "/it-it/product/b", "src": "", "text": ""},
    ]

    def posted(i: int) -> str:
        return f"2026-01-01T00:0{i}:00+00:00"

    Ls = listings_from_snapshots(
        rows,
        search_name="s",
        search_page_url="https://x",
        posted_at_for_index=posted,
    )
    assert len(Ls) == 2


def test_buycycle_parse_posted_at_iso():
    src = BuycycleSource()
    dt = src.parse_posted_at("2026-03-21T15:30:00+00:00")
    assert dt == datetime(2026, 3, 21, 15, 30, tzinfo=timezone.utc)


def test_buycycle_normalizes_listing_id():
    src = BuycycleSource()
    from bikefinder.sources.listing_ids import with_source_prefix

    raw = Listing(
        listing_id="1985562",
        url="https://buycycle.com/it-it/product/x",
        title="t",
        body="b",
        posted_at="2026-01-01T00:00:00+00:00",
        price=None,
        location=None,
        image_urls=[],
        search_name="s",
    )
    out = with_source_prefix(raw, src.source_id)
    assert out.listing_id == "buycycle:1985562"
    assert out.source == "buycycle"

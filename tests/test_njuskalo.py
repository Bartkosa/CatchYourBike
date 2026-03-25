from __future__ import annotations

from datetime import timezone
from pathlib import Path

import pytest

from bikefinder.sources.njuskalo import NjuskaloSource
from bikefinder.sources.njuskalo.serp_html import listings_from_search_html
from bikefinder.sources.njuskalo.urls import assert_njuskalo_search_url, build_serp_url

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "njuskalo_serp_min.html"


def test_assert_njuskalo_search_url_accepts_njuskalo():
    assert_njuskalo_search_url("https://www.njuskalo.hr/bicikli")


def test_assert_njuskalo_search_url_rejects_other_hosts():
    with pytest.raises(ValueError, match="Njuskalo search url"):
        assert_njuskalo_search_url("https://example.com/bicikli")


def test_build_serp_url_preserves_extra_params_and_sets_price_page():
    base = (
        "https://www.njuskalo.hr/bicikli?"
        "condition%5Bused%5D=1&page=9"
    )
    out = build_serp_url(
        base,
        price_min_eur=200,
        price_max_eur=900,
        page_1based=3,
    )
    assert "condition%5Bused%5D=1" in out
    assert "price%5Bmin%5D=200" in out
    assert "price%5Bmax%5D=900" in out
    assert "page=3" in out


def test_listings_from_search_html_fixture():
    html = _FIXTURE.read_text(encoding="utf-8")
    listings, total_results, total_pages = listings_from_search_html(html, "s")
    assert total_results == 100
    assert total_pages == 4
    assert len(listings) == 2
    assert listings[0].listing_id == "1"
    assert listings[0].title == "Promo dup"
    assert listings[0].url == "https://www.njuskalo.hr/gorska-kolesa/promo-dup-oglas-1"
    assert listings[0].price == "100 €"
    assert listings[0].location == "A, B"
    assert listings[1].listing_id == "15714468"
    assert listings[1].posted_at == "2026-03-22T19:43:06.000Z"
    assert "Lokacija: Celje, Center" in listings[1].body


def test_njuskalo_parse_posted_at_iso_z():
    src = NjuskaloSource()
    dt = src.parse_posted_at("2026-03-22T19:43:06.000Z")
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_njuskalo_parse_posted_at_slovenian_date():
    src = NjuskaloSource()
    dt = src.parse_posted_at("21.03.2026.")
    assert dt is not None
    assert dt.month == 3 and dt.day == 21 and dt.year == 2026

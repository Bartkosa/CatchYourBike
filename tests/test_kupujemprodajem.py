from __future__ import annotations

from pathlib import Path

import pytest

from bikefinder.models import Listing
from bikefinder.sources.kupujemprodajem import KupujemProdajemSource
from bikefinder.sources.kupujemprodajem.serp_html import (
    listings_from_search_html,
    serp_has_next_page,
)
from bikefinder.sources.kupujemprodajem.urls import (
    assert_kupujemprodajem_search_url,
    build_serp_url,
)
from bikefinder.sources.listing_ids import with_source_prefix

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "kupujemprodajem_serp_sample.html"


def test_kupujemprodajem_normalizes_listing_id():
    src = KupujemProdajemSource()
    raw = Listing(
        listing_id="12345678",
        url="https://www.kupujemprodajem.com/x/oglas/12345678",
        title="t",
        body="b",
        posted_at=None,
        price=None,
        location=None,
        image_urls=[],
        search_name="s",
    )
    out = with_source_prefix(raw, src.source_id)
    assert out.listing_id == "kupujemprodajem:12345678"
    assert out.source == "kupujemprodajem"


def test_listings_from_fixture_html():
    html = _FIXTURE.read_text(encoding="utf-8")
    listings, total_results, total_pages = listings_from_search_html(html, "fixture")
    assert len(listings) == 1
    assert listings[0].listing_id == "12345678"
    assert listings[0].title == "Sample road bike"
    assert listings[0].price == "350 €"
    assert listings[0].location == "Niš"
    assert listings[0].posted_at == "danas"
    assert total_results == 40
    assert total_pages == 5


def test_serp_has_next_page_from_title():
    html = "<html><head><title>Strana 2 od 30 - X</title></head></html>"
    assert serp_has_next_page(html) is True
    html2 = "<html><head><title>Strana 30 od 30 - X</title></head></html>"
    assert serp_has_next_page(html2) is False


def test_build_serp_url_merges_query():
    base = (
        "https://www.kupujemprodajem.com/bicikli/drumski-trkacki/grupa/912/919/2"
        "?categoryId=912&order=posted%20desc&page=9"
    )
    out = build_serp_url(base, price_min_eur=100, price_max_eur=900, page_1based=3)
    assert "page=3" in out
    assert "priceFrom=100" in out
    assert "priceTo=900" in out
    assert "hasPrice=yes" in out
    assert "categoryId=912" in out


def test_assert_url_rejects_foreign_host():
    with pytest.raises(ValueError, match="KupujemProdajem"):
        assert_kupujemprodajem_search_url("https://example.com/x")


def test_parse_posted_at_danas():
    src = KupujemProdajemSource()
    dt = src.parse_posted_at("danas")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_posted_at_iso():
    src = KupujemProdajemSource()
    dt = src.parse_posted_at("2026-03-20T12:00:00+01:00")
    assert dt is not None

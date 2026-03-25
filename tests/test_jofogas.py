from __future__ import annotations

from datetime import timezone
from pathlib import Path

import pytest

from bikefinder.sources.jofogas import JofogasSource
from bikefinder.sources.jofogas.serp_html import listings_from_search_html
from bikefinder.sources.jofogas.urls import assert_jofogas_search_url, build_serp_url

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "jofogas_serp_min.html"


def test_assert_jofogas_search_url_accepts_jofogas():
    assert_jofogas_search_url("https://www.jofogas.hu/magyarorszag/kerekparok-es-alkatreszek")


def test_assert_jofogas_search_url_rejects_other_hosts():
    with pytest.raises(ValueError, match="Jófogás search url"):
        assert_jofogas_search_url("https://example.com/x")


def test_build_serp_url_preserves_extra_params_and_sets_price_page():
    base = "https://www.jofogas.hu/magyarorszag/kerekparok-es-alkatreszek?f=p&sp=3&o=9"
    out = build_serp_url(
        base,
        price_min_huf=60000,
        price_max_huf=480000,
        page_1based=3,
    )
    assert "f=p" in out
    assert "sp=3" in out
    assert "min_price=60000" in out
    assert "max_price=480000" in out
    assert "o=3" in out


def test_listings_from_search_html_fixture():
    html = _FIXTURE.read_text(encoding="utf-8")
    listings, total_results, total_pages = listings_from_search_html(html, "s")
    assert total_results == 72
    assert total_pages == 2
    assert len(listings) == 1
    assert listings[0].listing_id == "159803322"
    assert listings[0].title == "Test bike"
    assert "Line one" in listings[0].body and "Line two" in listings[0].body
    assert listings[0].price == "50 000 Ft"
    assert listings[0].location == "Pest"
    assert listings[0].image_urls == ["https://img.jofogas.hu/620x620aspect/x.jpg"]
    assert listings[0].posted_at is not None
    assert listings[0].posted_at.startswith("2024-03-22")


def test_jofogas_parse_posted_at_iso():
    src = JofogasSource()
    dt = src.parse_posted_at("2026-03-22T19:43:06.000Z")
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_jofogas_parse_posted_at_unix_string():
    src = JofogasSource()
    dt = src.parse_posted_at("1711108800")
    assert dt is not None
    assert dt.year == 2024 and dt.month == 3 and dt.day == 22

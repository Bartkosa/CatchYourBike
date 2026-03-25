from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qsl

import pytest

from bikefinder.models import Listing
from bikefinder.sources.facebook import FacebookSource
from bikefinder.sources.facebook.graphql_harvest import harvest_marketplace_hints
from bikefinder.sources.facebook.parse import listing_from_card, listings_from_cards
from bikefinder.sources.facebook.urls import (
    assert_facebook_marketplace_url,
    build_fetch_url,
    simplify_np_marketplace_url,
)


def test_assert_facebook_marketplace_url_ok():
    assert_facebook_marketplace_url(
        "https://www.facebook.com/marketplace/search/?query=bicycle"
    )


def test_assert_facebook_marketplace_url_np_location_hub():
    """Location-hub search URLs use /marketplace/np/<id>/search (e.g. Padua + radius)."""
    assert_facebook_marketplace_url(
        "https://www.facebook.com/marketplace/np/110539155637396/search?query=bici&radius=500"
    )


def test_assert_facebook_marketplace_url_rejects_non_facebook():
    with pytest.raises(ValueError, match="facebook"):
        assert_facebook_marketplace_url("https://example.com/marketplace/search/")


def test_assert_facebook_marketplace_url_rejects_non_marketplace_path():
    with pytest.raises(ValueError, match="marketplace"):
        assert_facebook_marketplace_url("https://www.facebook.com/groups/123")


def test_build_fetch_url_merges_prices_and_default_sort():
    u = build_fetch_url(
        "https://www.facebook.com/marketplace/search/?query=bike",
        price_min_eur=150,
        price_max_eur=1200,
    )
    q = dict(parse_qsl(u.split("?", 1)[1]))
    assert q["minPrice"] == "150"
    assert q["maxPrice"] == "1200"
    assert q["sortBy"] == "creation_time_descend"
    assert q["exact"] == "false"


def test_build_fetch_url_preserves_explicit_sort():
    u = build_fetch_url(
        "https://www.facebook.com/marketplace/search/?query=bike&sortBy=distance",
        price_min_eur=1,
        price_max_eur=9,
    )
    q = dict(parse_qsl(u.split("?", 1)[1]))
    assert q["sortBy"] == "distance"


def test_simplify_np_marketplace_url_drops_partner_keys():
    u = (
        "https://www.facebook.com/marketplace/np/110539155637396/search?"
        "minPrice=150&maxPrice=1200&query=bici&radius=500&exact=false&"
        "partner_ids%5B0%5D=1311206086847592&hide_organic_listings=false&partner_selected=true"
    )
    out = simplify_np_marketplace_url(u)
    q = dict(parse_qsl(out.split("?", 1)[1]))
    assert q["query"] == "bici"
    assert "partner_selected" not in q
    assert "hide_organic_listings" not in q
    assert "partner_ids[0]" not in q


def test_simplify_np_noop_for_non_np():
    u = "https://www.facebook.com/marketplace/search/?query=bike"
    assert simplify_np_marketplace_url(u) == u


def test_build_fetch_url_np_search_is_passthrough_ignores_yaml_prices():
    base = (
        "https://www.facebook.com/marketplace/np/110539155637396/search?"
        "query=bici&radius=500&daysSinceListed=1&exact=false&"
        "partner_ids%5B0%5D=1311206086847592&hide_organic_listings=false&partner_selected=true"
    )
    u = build_fetch_url(base, price_min_eur=1, price_max_eur=99999)
    assert u == base


def test_build_fetch_url_np_keeps_explicit_sort_without_reencode():
    inp = "https://www.facebook.com/marketplace/np/99/search?sortBy=creation_time_descend&query=x"
    u = build_fetch_url(inp, price_min_eur=150, price_max_eur=1200)
    assert u == inp


def test_harvest_marketplace_hints_finds_creation_time():
    out: dict = {}
    payload = {
        "data": {
            "node": {
                "id": "123456789012345",
                "title": "Road bike",
                "creation_time": 1_700_000_000,
            }
        }
    }
    harvest_marketplace_hints(payload, out)
    assert "123456789012345" in out
    assert out["123456789012345"]["creation_ts"] == 1_700_000_000
    assert out["123456789012345"]["title"] == "Road bike"


def test_harvest_marketplace_hints_attaches_primary_listing_photo():
    out: dict = {}
    payload = {
        "id": "1234567890123456",
        "title": "E-bike",
        "primary_listing_photo": {
            "uri": "https://scontent.ffco4-1.fna.fbcdn.net/v/t39.84726-6/x.jpg"
        },
    }
    harvest_marketplace_hints(payload, out)
    assert "1234567890123456" in out
    urls = out["1234567890123456"].get("image_urls") or []
    assert urls
    assert "fbcdn.net" in urls[0]


def test_listing_from_card_builds_listing():
    L = listing_from_card(
        {
            "id": "999888777666",
            "href": "https://www.facebook.com/marketplace/item/999888777666/",
            "title": "Trek FX",
            "price": "€450",
            "location": "Padova",
            "image_urls": ["https://img.example.com/a.jpg"],
        },
        search_name="fb_test",
        search_page_url="https://www.facebook.com/marketplace/search/?q=x",
        posted_at="2026-03-20T12:00:00+00:00",
    )
    assert L is not None
    assert L.listing_id == "999888777666"
    assert "marketplace/item/999888777666" in L.url
    assert L.title == "Trek FX"
    assert L.price == "€450"
    assert L.location == "Padova"
    assert L.image_urls == ["https://img.example.com/a.jpg"]


def test_listings_from_cards_uses_creation_ts():
    epoch = 1_700_000_000
    Ls = listings_from_cards(
        [
            {
                "id": "111",
                "href": "/marketplace/item/111",
                "title": "A",
                "creation_ts": epoch,
                "image_urls": [],
            }
        ],
        search_name="s",
        search_page_url="https://www.facebook.com/marketplace/search/",
        posted_at_for_index=lambda _i: None,
    )
    assert len(Ls) == 1
    expected = datetime.fromtimestamp(epoch, tz=timezone.utc).replace(
        microsecond=0
    ).isoformat()
    assert Ls[0].posted_at == expected


def test_facebook_parse_posted_at_iso_and_relative():
    src = FacebookSource()
    dt = src.parse_posted_at("2026-01-15T10:00:00+00:00")
    assert dt is not None
    assert dt.tzinfo is not None
    dt2 = src.parse_posted_at("2 giorni fa")
    assert dt2 is not None


def test_facebook_normalizes_listing_id():
    src = FacebookSource()
    raw = Listing(
        listing_id="123",
        url="https://www.facebook.com/marketplace/item/123/",
        title="t",
        body="b",
        posted_at=None,
        price=None,
        location=None,
        image_urls=[],
        search_name="s",
    )
    from bikefinder.sources.listing_ids import with_source_prefix

    out = with_source_prefix(raw, src.source_id)
    assert out.listing_id == "facebook:123"
    assert out.source == "facebook"


def test_app_config_facebook_min_scroll_must_not_exceed_cap():
    from bikefinder.config import AppConfig

    base = {
        "searches": [
            {
                "name": "fb",
                "url": "https://www.facebook.com/marketplace/search/?query=bike",
                "source": "facebook",
            }
        ],
        "database_url": "postgresql://u:p@localhost:5432/db",
        "reference_images": ["x.jpg"],
    }
    AppConfig.model_validate(
        {
            **base,
            "facebook_min_scroll_rounds": 8,
            "facebook_scroll_rounds_cap": 120,
        }
    )
    with pytest.raises(ValueError, match="facebook_min_scroll_rounds"):
        AppConfig.model_validate(
            {
                **base,
                "facebook_min_scroll_rounds": 200,
                "facebook_scroll_rounds_cap": 50,
            }
        )

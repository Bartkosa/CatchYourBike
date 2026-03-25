from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlparse

import pytest

from bikefinder.sources.willhaben import WillhabenSource
from bikefinder.sources.willhaben.serp_html import listings_from_search_html
from bikefinder.sources.willhaben.urls import assert_willhaben_search_url, build_serp_url

def _q(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlparse(url).query, keep_blank_values=True))


def test_build_serp_url_preserves_and_sets_params():
    base = (
        "https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz/fahrraeder-4552"
        "?topicId=1001&srcType=feed-popular-cat&srcAdd=Fahrraeder"
    )
    u = build_serp_url(base, price_min_eur=150, price_max_eur=1200, page_1based=2)
    q = _q(u)
    assert q["topicId"] == "1001"
    assert q["srcType"] == "feed-popular-cat"
    assert q["srcAdd"] == "Fahrraeder"
    assert q["PRICE_FROM"] == "150"
    assert q["PRICE_TO"] == "1200"
    assert q["sort"] == "1"
    assert q["page"] == "2"


def test_assert_willhaben_search_url_rejects_non_marktplatz():
    with pytest.raises(ValueError, match="marktplatz"):
        assert_willhaben_search_url("https://www.willhaben.at/iad/kaufen-und-verkaufen/")


def test_listings_from_search_html_minimal():
    sr = {
        "rowsFound": 60,
        "rowsReturned": 30,
        "advertSummaryList": {
            "advertSummary": [
                {
                    "id": "123",
                    "description": "Test Bike",
                    "contextLinkList": {
                        "contextLink": [
                            {
                                "id": "seoSelfLink",
                                "relativePath": "/atverz/kaufen-und-verkaufen/d/test-bike-123/",
                            }
                        ]
                    },
                    "attributes": {
                        "attribute": [
                            {"name": "BODY_DYN", "values": ["Short body"]},
                            {"name": "CHANGED_String", "values": ["2026-03-21T12:00:00Z"]},
                            {"name": "PRICE_FOR_DISPLAY", "values": ["€ 500"]},
                            {"name": "LOCATION", "values": ["Wien"]},
                            {"name": "POSTCODE", "values": ["1010"]},
                        ]
                    },
                    "advertImageList": {
                        "advertImage": [
                            {"mainImageUrl": "https://cache.willhaben.at/mmo/x.jpg"}
                        ]
                    },
                }
            ]
        },
    }
    payload = {"props": {"pageProps": {"searchResult": sr}}}
    html = (
        "<!DOCTYPE html><html><body>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        "</body></html>"
    )
    listings, total_results, total_pages = listings_from_search_html(html, "wh_test")
    assert total_results == 60
    assert total_pages == 2
    assert len(listings) == 1
    L = listings[0]
    assert L.listing_id == "123"
    assert L.title == "Test Bike"
    assert L.body == "Short body"
    assert L.price == "€ 500"
    assert L.posted_at == "2026-03-21T12:00:00Z"
    assert L.location == "1010 Wien"
    assert L.url == "https://www.willhaben.at/iad/kaufen-und-verkaufen/d/test-bike-123/"
    assert L.image_urls == ["https://cache.willhaben.at/mmo/x.jpg"]


def test_willhaben_parse_posted_at_iso_z():
    src = WillhabenSource()
    dt = src.parse_posted_at("2026-03-21T12:00:00Z")
    assert dt == datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)


def test_willhaben_parse_posted_at_heute_uhr():
    src = WillhabenSource()
    assert src.parse_posted_at("Heute, 14:28 Uhr") is not None

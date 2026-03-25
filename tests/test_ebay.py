from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from bikefinder.config import AppConfig, SearchEntry
from bikefinder.sources.ebay.client import (
    build_search_filter_parts,
    request_url_for_log,
    reset_oauth_token_cache,
)
from bikefinder.sources.ebay.parse import listings_from_ebay_search_response, parse_ebay_item
from bikefinder.sources.ebay import EbaySource


@pytest.fixture(autouse=True)
def _clear_ebay_token():
    reset_oauth_token_cache()
    yield
    reset_oauth_token_cache()


def test_request_url_for_log_omits_q_when_empty():
    url = request_url_for_log(
        "EBAY_IT",
        "",
        "price:[1..2],priceCurrency:EUR",
        50,
        0,
        category_ids="177831",
    )
    assert "category_ids=177831" in url
    assert "q=" not in url


def test_build_search_filter_parts_eur():
    f = build_search_filter_parts(
        price_min=150,
        price_max=1200,
        price_currency="EUR",
        item_location_country="SI",
        buying_options="AUCTION|FIXED_PRICE",
    )
    assert "price:[150..1200]" in f
    assert "priceCurrency:EUR" in f
    assert "itemLocationCountry:SI" in f
    assert "buyingOptions:{AUCTION|FIXED_PRICE}" in f


def test_parse_ebay_item_minimal():
    raw = {
        "itemId": "v1|1|0",
        "title": "Road bike",
        "itemWebUrl": "https://www.ebay.it/itm/123",
        "itemOriginDate": "2026-03-20T10:00:00.000Z",
        "price": {"value": "499.00", "currency": "EUR"},
        "image": {"imageUrl": "https://i.ebayimg.com/x.jpg"},
        "itemLocation": {"city": "Milano", "country": "IT"},
    }
    L = parse_ebay_item(raw, "s1")
    assert L is not None
    assert L.listing_id == "v1|1|0"
    assert L.posted_at == "2026-03-20T10:00:00.000Z"
    assert "499" in (L.price or "")
    assert L.image_urls


def test_listings_from_ebay_search_response():
    payload = {
        "itemSummaries": [
            {
                "itemId": "a",
                "title": "A",
                "itemWebUrl": "https://x/a",
                "itemOriginDate": "2026-01-01T00:00:00.000Z",
            },
            {
                "itemId": "a",
                "title": "dup",
                "itemWebUrl": "https://x/a",
                "itemOriginDate": "2026-01-01T00:00:00.000Z",
            },
        ]
    }
    Ls = listings_from_ebay_search_response(payload, "n")
    assert len(Ls) == 1


def _minimal_ebay_cfg(**kwargs) -> AppConfig:
    data = {
        "database_url": "postgresql://u:p@localhost:5432/db",
        "searches": [
            SearchEntry(
                name="ebay_test",
                source="ebay",
                url="",
                ebay_marketplace_ids=["EBAY_IT"],
            )
        ],
        "max_pages_per_search": 1,
        "delay_seconds": 0,
    }
    data.update(kwargs)
    return AppConfig.model_validate(data)


@patch.dict(
    os.environ,
    {"EBAY_CLIENT_ID": "id", "EBAY_CLIENT_SECRET": "secret"},
    clear=False,
)
@patch("bikefinder.sources.ebay.search_item_summaries")
@patch("bikefinder.sources.ebay.get_application_access_token")
def test_ebay_source_fetch_mocked(mock_token, mock_search):
    mock_token.return_value = "tok"
    mock_search.return_value = {
        "itemSummaries": [
            {
                "itemId": "v1|9|0",
                "title": "Bike",
                "itemWebUrl": "https://www.ebay.it/itm/x",
                "itemOriginDate": "2026-03-21T12:00:00.000Z",
                "price": {"value": "200", "currency": "EUR"},
            }
        ]
    }

    cfg = _minimal_ebay_cfg()
    src = EbaySource()
    listings, reached = src.fetch_search_pages(cfg, "", "ebay_test", start_page_index=0)
    assert len(listings) == 1
    assert listings[0].listing_id == "ebay:v1|9|0"
    assert listings[0].source == "ebay"
    assert reached is True
    mock_token.assert_called_once()
    mock_search.assert_called_once()
    assert mock_search.call_args.kwargs.get("category_ids") == "177831"
    assert "(bicycle" in (mock_search.call_args.kwargs.get("q") or "")


@patch.dict(
    os.environ,
    {"EBAY_CLIENT_ID": "id", "EBAY_CLIENT_SECRET": "secret"},
    clear=False,
)
@patch("bikefinder.sources.ebay.search_item_summaries")
@patch("bikefinder.sources.ebay.get_application_access_token")
def test_ebay_source_no_category_when_default_empty(mock_token, mock_search):
    mock_token.return_value = "tok"
    mock_search.return_value = {"itemSummaries": []}
    cfg = _minimal_ebay_cfg(ebay_default_category_ids="")
    EbaySource().fetch_search_pages(cfg, "", "ebay_test", start_page_index=0)
    assert mock_search.call_args.kwargs.get("category_ids") is None


def test_subito_search_requires_url():
    with pytest.raises(ValueError, match="url is required"):
        SearchEntry(name="x", source="subito", url="")


def test_ebay_search_allows_empty_url():
    s = SearchEntry(name="e", source="ebay", url="")
    assert s.source == "ebay"


@patch.dict(
    os.environ,
    {"EBAY_CLIENT_ID": "id", "EBAY_CLIENT_SECRET": "secret"},
    clear=False,
)
@patch("bikefinder.sources.ebay.search_item_summaries")
@patch("bikefinder.sources.ebay.get_application_access_token")
def test_ebay_source_category_only_passes_empty_q(mock_token, mock_search):
    mock_token.return_value = "tok"
    mock_search.return_value = {"itemSummaries": []}
    cfg = _minimal_ebay_cfg(
        searches=[
            SearchEntry(
                name="ebay_test",
                source="ebay",
                url="",
                ebay_marketplace_ids=["EBAY_IT"],
                ebay_query="",
            )
        ],
    )
    EbaySource().fetch_search_pages(cfg, "", "ebay_test", start_page_index=0)
    assert mock_search.call_args.kwargs.get("q") == ""
    assert mock_search.call_args.kwargs.get("category_ids") == "177831"


def test_ebay_category_only_requires_category_ids():
    cfg = _minimal_ebay_cfg(
        ebay_default_category_ids="",
        searches=[
            SearchEntry(
                name="ebay_test",
                source="ebay",
                url="",
                ebay_marketplace_ids=["EBAY_IT"],
                ebay_query="",
            )
        ],
    )
    with pytest.raises(ValueError, match="keywordless"):
        EbaySource().fetch_search_pages(cfg, "", "ebay_test", start_page_index=0)


def test_ebay_parse_posted_at():
    src = EbaySource()
    dt = src.parse_posted_at("2026-03-21T12:00:00.000Z")
    assert dt is not None
    assert dt.year == 2026
    assert dt.tzinfo is not None

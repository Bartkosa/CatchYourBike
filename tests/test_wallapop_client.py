import pytest

from bikefinder.sources.wallapop.client import (
    _WALLAPOP_LOAD_MORE_PATTERN,
    _assert_supported_search_url,
    _new_items_from_captured_range,
    _pick_last_section_payload,
    _raw_listing_ids_from_payload,
    _synthetic_section_payload,
    _url_without_page_param,
)


def test_wallapop_v1_accepts_expected_url():
    _assert_supported_search_url(
        "https://it.wallapop.com/search?category_id=17000&min_sale_price=200&max_sale_price=1000&order_by=newest"
    )


def test_wallapop_v1_rejects_other_url():
    with pytest.raises(ValueError, match="supports only this URL"):
        _assert_supported_search_url(
            "https://it.wallapop.com/search?category_id=16000&min_sale_price=100&max_sale_price=1000&order_by=newest"
        )


def test_wallapop_v1_accepts_equivalent_url_with_extra_params():
    _assert_supported_search_url(
        "https://it.wallapop.com/search/?order_by=newest&category_id=17000&max_sale_price=1000&min_sale_price=200&utm_source=test"
    )


def test_wallapop_v1_accepts_different_price_band():
    _assert_supported_search_url(
        "https://it.wallapop.com/search?category_id=17000&min_sale_price=150&max_sale_price=1200&order_by=newest"
    )


def test_pick_last_section_payload_prefers_latest_non_empty():
    p1 = {"data": {"section": {"items": [{"id": "a"}]}}}
    p2 = {"data": {"section": {"items": [{"id": "b"}]}}}
    assert _pick_last_section_payload([p1, p2]) is p2


def test_raw_listing_ids_from_payload():
    p = {"data": {"section": {"items": [{"id": "x"}, {"item_id": "y"}]}}}
    assert _raw_listing_ids_from_payload(p) == frozenset({"x", "y"})


def test_url_without_page_param():
    u = "https://it.wallapop.com/search?category_id=17000&page=3&order_by=newest"
    assert "page=" not in _url_without_page_param(u)
    assert "category_id=17000" in _url_without_page_param(u)


def test_synthetic_section_payload_carries_meta():
    tpl = {"data": {"section": {"items": []}}, "meta": {"next_page": "tok"}}
    syn = _synthetic_section_payload([{"id": "a"}], tpl)
    assert syn["data"]["section"]["items"] == [{"id": "a"}]
    assert syn["meta"] == {"next_page": "tok"}


def test_wallapop_load_more_pattern_matches_italian():
    assert _WALLAPOP_LOAD_MORE_PATTERN.search("Carica altro")
    assert not _WALLAPOP_LOAD_MORE_PATTERN.search("foo")


def test_new_items_from_captured_range_dedupes():
    captured = [
        {"data": {"section": {"items": [{"id": "1"}, {"id": "2"}]}}},
        {"data": {"section": {"items": [{"id": "2"}, {"id": "3"}]}}},
    ]
    known: set[str] = set()
    new, _tpl = _new_items_from_captured_range(captured, 0, len(captured), known)
    assert [x["id"] for x in new] == ["1", "2", "3"]
    assert known == {"1", "2", "3"}

import json

from bikefinder.sources.subito.serp_html import listings_from_search_html


def test_listings_from_minimal_next_data():
    payload = {
        "props": {
            "pageProps": {
                "initialState": {
                    "items": {
                        "total": 1,
                        "totalPages": 1,
                        "list": [
                            {
                                "item": {
                                    "kind": "AdItem",
                                    "urn": "id:ad:uuid-here:list:12345",
                                    "subject": "Test MTB",
                                    "body": "Great bike",
                                    "geo": {
                                        "town": {"value": "Padova"},
                                        "city": {"value": "Padova"},
                                        "region": {"value": "Veneto"},
                                    },
                                    "features": {
                                        "/price": {
                                            "values": [{"key": "500", "value": "500 €"}],
                                        },
                                    },
                                    "images": [
                                        {"cdnBaseUrl": "https://images.example/img1"},
                                    ],
                                    "urls": {
                                        "default": "https://www.subito.it/biciclette/test-padova-12345.htm",
                                    },
                                },
                            },
                        ],
                    },
                },
            },
        },
    }
    html = (
        '<!DOCTYPE html><html><body>'
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        "</body></html>"
    )
    listings, total, pages = listings_from_search_html(html, "test_search")
    assert total == 1
    assert pages == 1
    assert len(listings) == 1
    L = listings[0]
    assert L.listing_id == "12345"
    assert L.title == "Test MTB"
    assert "Padova" in (L.location or "")
    assert L.price
    assert L.url.endswith("12345.htm")
    assert "rule=gallery-desktop-2x-auto" in L.image_urls[0]


def _minimal_ad_item(listing_id: str, title: str) -> dict:
    return {
        "kind": "AdItem",
        "urn": f"id:ad:uuid:list:{listing_id}",
        "subject": title,
        "body": "",
        "geo": {},
        "features": {},
        "images": [{"cdnBaseUrl": "https://images.example/x"}],
        "urls": {"default": f"https://www.subito.it/biciclette/x-{listing_id}.htm"},
    }


def test_listings_merge_gallery_list_without_duplicates():
    """galleryList often contains ads not present in list; overlaps must dedupe."""
    payload = {
        "props": {
            "pageProps": {
                "initialState": {
                    "items": {
                        "total": 3,
                        "totalPages": 1,
                        "list": [{"item": _minimal_ad_item("111", "Main list bike")}],
                        "galleryList": [
                            _minimal_ad_item("222", "Gallery only bike"),
                            _minimal_ad_item("111", "Dup from gallery"),
                        ],
                    },
                },
            },
        },
    }
    html = (
        '<!DOCTYPE html><html><body>'
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        "</body></html>"
    )
    listings, total, pages = listings_from_search_html(html, "test_search")
    assert total == 3
    assert pages == 1
    ids = {L.listing_id for L in listings}
    assert ids == {"111", "222"}
    by_id = {L.listing_id: L for L in listings}
    assert by_id["111"].title == "Main list bike"
    assert by_id["222"].title == "Gallery only bike"

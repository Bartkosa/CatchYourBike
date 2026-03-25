from bikefinder.sources.wallapop.parse_api import (
    listings_from_wallapop_payload,
    parse_wallapop_item,
)


def test_parse_wallapop_item_minimal():
    raw = {
        "id": "123",
        "web_slug": "bicicletta-da-corsa-123",
        "title": "Bici corsa",
        "description": "Ottime condizioni",
        "sale_price": {"amount": 550},
        "location": {"city": "Milano", "region": "Lombardia"},
        "images": [{"url": "https://images.example/1.jpg"}],
        "creation_date": "2026-03-20T10:00:00+00:00",
    }
    listing = parse_wallapop_item(raw, "walla_test")
    assert listing is not None
    assert listing.listing_id == "123"
    assert listing.url.startswith("https://it.wallapop.com/item/")
    assert listing.title == "Bici corsa"
    assert listing.price == "550"
    assert listing.location == "Milano, Lombardia"
    assert listing.image_urls == ["https://images.example/1.jpg"]
    assert listing.posted_at == "2026-03-20 10:00:00"


def test_listings_from_wallapop_payload_dedup_and_cursor():
    payload = {
        "search_objects": [
            {"id": "1", "url": "https://it.wallapop.com/item/a", "title": "A"},
            {"id": "1", "url": "https://it.wallapop.com/item/a", "title": "A dup"},
            {"id": "2", "url": "https://it.wallapop.com/item/b", "title": "B"},
        ],
        "next_page": "cursor-xyz",
    }
    listings, cursor = listings_from_wallapop_payload(payload, "walla_test")
    assert [x.listing_id for x in listings] == ["1", "2"]
    assert cursor == "cursor-xyz"


def test_listings_from_wallapop_section_payload_shape():
    payload = {
        "data": {
            "section": {
                "items": [
                    {
                        "id": "10",
                        "title": "Bike 10",
                        "description": "desc",
                        "price": {"amount": 480},
                        "web_slug": "bike-10",
                        "images": [
                            {
                                "id": "pic1",
                                "urls": {
                                    "small": "https://cdn.example/s.jpg",
                                    "medium": "https://cdn.example/m.jpg",
                                    "big": "https://cdn.example/b.jpg",
                                },
                            }
                        ],
                        "created_at": "2026-03-20T10:00:00+00:00",
                    }
                ],
                "search_id": "sid-1",
            },
            "tracking": {"search_id": "sid-1"},
        },
        "meta": {"search_id": "sid-1"},
    }
    listings, cursor = listings_from_wallapop_payload(payload, "walla_test")
    assert len(listings) == 1
    assert listings[0].listing_id == "10"
    assert listings[0].image_urls == ["https://cdn.example/b.jpg"]
    assert listings[0].posted_at == "2026-03-20 10:00:00"
    assert cursor == "sid-1"


def test_wallapop_posted_at_millis_string_normalized():
    raw = {
        "id": "abc",
        "web_slug": "x",
        "title": "t",
        "created_at": "1774038075460",
    }
    listing = parse_wallapop_item(raw, "s")
    assert listing is not None
    assert listing.posted_at == "2026-03-20 20:21:15"


def test_wallapop_posted_at_millis_int_normalized():
    raw = {
        "id": "abc",
        "web_slug": "x",
        "title": "t",
        "created_at": 1774038075460,
    }
    listing = parse_wallapop_item(raw, "s")
    assert listing is not None
    assert listing.posted_at == "2026-03-20 20:21:15"


def test_wallapop_next_cursor_reads_meta_next_page():
    payload = {
        "meta": {"next_page": "next-token-abc"},
        "data": {"section": {"items": [{"id": "z", "web_slug": "x", "title": "t"}]}},
    }
    _listings, cursor = listings_from_wallapop_payload(payload, "s")
    assert cursor == "next-token-abc"


def test_wallapop_image_urls_prefers_big_under_urls_key():
    raw = {
        "id": "x",
        "web_slug": "item-x",
        "title": "t",
        "images": [
            {
                "urls": {
                    "small": "https://cdn/s.png",
                    "big": "https://cdn/b.png",
                }
            },
            {
                "urls": {
                    "medium": "https://cdn/m2.png",
                }
            },
        ],
    }
    listing = parse_wallapop_item(raw, "s")
    assert listing is not None
    assert listing.image_urls == [
        "https://cdn/b.png",
        "https://cdn/m2.png",
    ]

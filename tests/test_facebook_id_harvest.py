from bikefinder.sources.facebook.id_harvest import (
    harvest_listing_ids_from_json,
    harvest_listing_ids_from_text,
)


def test_harvest_listing_ids_from_graphql_like_blob():
    body = (
        '{"story":{"url":"https://www.facebook.com/marketplace/item/1234567890123456/"}}'
        ' and /marketplace/item/9876543210987654?ref=search'
    )
    ids = harvest_listing_ids_from_text(body)
    assert "1234567890123456" in ids
    assert "9876543210987654" in ids


def test_harvest_ignores_short_numeric_segments():
    body = 'marketplace/item/12345'
    assert harvest_listing_ids_from_text(body) == []


def test_harvest_urlencoded_path():
    body = "foo marketplace%2Fitem%2F1234567890123456 bar"
    assert "1234567890123456" in harvest_listing_ids_from_text(body)


def test_harvest_json_escaped_slashes():
    body = r'{"u":"https:\/\/www.facebook.com\/marketplace\/item\/9876543210987654\/"}'
    assert "9876543210987654" in harvest_listing_ids_from_text(body)


def test_harvest_listing_ids_from_json_typename():
    obj = {
        "data": {
            "node": {
                "__typename": "MarketplaceListing",
                "id": "1234567890123456",
                "listing_price": {"amount": "1"},
            }
        }
    }
    assert "1234567890123456" in harvest_listing_ids_from_json(obj)


def test_harvest_listing_ids_from_json_price_hint():
    obj = {"edges": [{"node": {"id": "1111111111111111", "formatted_price": {"text": "€5"}}}]}
    assert "1111111111111111" in harvest_listing_ids_from_json(obj)


def test_harvest_listing_ids_from_json_marketplace_listing_id_key():
    obj = {"payload": {"marketplace_listing_id": "2222222222222222"}}
    assert "2222222222222222" in harvest_listing_ids_from_json(obj)

import pytest

from bikefinder.models import Listing
from bikefinder.sources import LISTING_SOURCES, get_listing_source
from bikefinder.sources.listing_ids import with_source_prefix
from bikefinder.sources.ebay import EbaySource
from bikefinder.sources.kleinanzeigen import KleinanzeigenSource
from bikefinder.sources.subito import SubitoSource
from bikefinder.sources.wallapop import WallapopSource
from bikefinder.sources.buycycle import BuycycleSource
from bikefinder.sources.facebook import FacebookSource
from bikefinder.sources.willhaben import WillhabenSource
from bikefinder.sources.bolha import BolhaSource
from bikefinder.sources.jofogas import JofogasSource
from bikefinder.sources.njuskalo import NjuskaloSource
from bikefinder.sources.kupujemprodajem import KupujemProdajemSource


def test_registry_contains_subito():
    assert "subito" in LISTING_SOURCES
    assert get_listing_source("subito") is LISTING_SOURCES["subito"]


def test_registry_contains_wallapop():
    assert "wallapop" in LISTING_SOURCES
    assert get_listing_source("wallapop") is LISTING_SOURCES["wallapop"]


def test_registry_contains_ebay():
    assert "ebay" in LISTING_SOURCES
    assert get_listing_source("ebay") is LISTING_SOURCES["ebay"]


def test_registry_contains_kleinanzeigen():
    assert "kleinanzeigen" in LISTING_SOURCES
    assert get_listing_source("kleinanzeigen") is LISTING_SOURCES["kleinanzeigen"]
    assert isinstance(LISTING_SOURCES["kleinanzeigen"], KleinanzeigenSource)


def test_registry_contains_willhaben():
    assert "willhaben" in LISTING_SOURCES
    assert get_listing_source("willhaben") is LISTING_SOURCES["willhaben"]
    assert isinstance(LISTING_SOURCES["willhaben"], WillhabenSource)


def test_registry_contains_buycycle():
    assert "buycycle" in LISTING_SOURCES
    assert get_listing_source("buycycle") is LISTING_SOURCES["buycycle"]
    assert isinstance(LISTING_SOURCES["buycycle"], BuycycleSource)


def test_registry_contains_facebook():
    assert "facebook" in LISTING_SOURCES
    assert get_listing_source("facebook") is LISTING_SOURCES["facebook"]
    assert isinstance(LISTING_SOURCES["facebook"], FacebookSource)


def test_subito_normalizes_listing_id():
    src = SubitoSource()
    raw = Listing(
        listing_id="999",
        url="https://www.subito.it/x.htm",
        title="t",
        body="b",
        posted_at=None,
        price=None,
        location=None,
        image_urls=[],
        search_name="s",
    )
    out = with_source_prefix(raw, src.source_id)
    assert out.listing_id == "subito:999"
    assert out.source == "subito"


def test_unknown_source_raises():
    with pytest.raises(ValueError, match="Unknown listing source"):
        get_listing_source("not_a_real_marketplace")


def test_ebay_normalizes_listing_id():
    src = EbaySource()
    raw = Listing(
        listing_id="v1|x|0",
        url="https://www.ebay.it/itm/1",
        title="t",
        body="",
        posted_at="2026-01-01T00:00:00.000Z",
        price=None,
        location=None,
        image_urls=[],
        search_name="s",
    )
    out = with_source_prefix(raw, src.source_id)
    assert out.listing_id == "ebay:v1|x|0"
    assert out.source == "ebay"


def test_kleinanzeigen_normalizes_listing_id():
    src = KleinanzeigenSource()
    raw = Listing(
        listing_id="12345",
        url="https://www.kleinanzeigen.de/s-anzeige/x/12345",
        title="t",
        body="b",
        posted_at=None,
        price=None,
        location=None,
        image_urls=[],
        search_name="s",
    )
    out = with_source_prefix(raw, src.source_id)
    assert out.listing_id == "kleinanzeigen:12345"
    assert out.source == "kleinanzeigen"


def test_wallapop_normalizes_listing_id():
    src = WallapopSource()
    raw = Listing(
        listing_id="abc123",
        url="https://it.wallapop.com/item/x",
        title="t",
        body="b",
        posted_at=None,
        price=None,
        location=None,
        image_urls=[],
        search_name="s",
    )
    out = with_source_prefix(raw, src.source_id)
    assert out.listing_id == "wallapop:abc123"
    assert out.source == "wallapop"


def test_willhaben_normalizes_listing_id():
    src = WillhabenSource()
    raw = Listing(
        listing_id="1980442775",
        url="https://www.willhaben.at/iad/kaufen-und-verkaufen/d/x-1980442775/",
        title="t",
        body="b",
        posted_at=None,
        price=None,
        location=None,
        image_urls=[],
        search_name="s",
    )
    out = with_source_prefix(raw, src.source_id)
    assert out.listing_id == "willhaben:1980442775"
    assert out.source == "willhaben"


def test_bolha_normalizes_listing_id():
    src = BolhaSource()
    raw = Listing(
        listing_id="15714468",
        url="https://www.bolha.com/kolesa/x-oglas-15714468",
        title="t",
        body="b",
        posted_at=None,
        price=None,
        location=None,
        image_urls=[],
        search_name="s",
    )
    out = with_source_prefix(raw, src.source_id)
    assert out.listing_id == "bolha:15714468"
    assert out.source == "bolha"


def test_registry_contains_njuskalo():
    assert "njuskalo" in LISTING_SOURCES
    assert get_listing_source("njuskalo") is LISTING_SOURCES["njuskalo"]
    assert isinstance(LISTING_SOURCES["njuskalo"], NjuskaloSource)


def test_registry_contains_jofogas():
    assert "jofogas" in LISTING_SOURCES
    assert get_listing_source("jofogas") is LISTING_SOURCES["jofogas"]
    assert isinstance(LISTING_SOURCES["jofogas"], JofogasSource)


def test_jofogas_normalizes_listing_id():
    src = JofogasSource()
    raw = Listing(
        listing_id="159803322",
        url="https://www.jofogas.hu/pest/Bike_159803322.htm",
        title="t",
        body="b",
        posted_at=None,
        price=None,
        location=None,
        image_urls=[],
        search_name="s",
    )
    out = with_source_prefix(raw, src.source_id)
    assert out.listing_id == "jofogas:159803322"
    assert out.source == "jofogas"


def test_njuskalo_normalizes_listing_id():
    src = NjuskaloSource()
    raw = Listing(
        listing_id="48796313",
        url="https://www.njuskalo.hr/gradski-bicikli/biciklo-oglas-48796313",
        title="t",
        body="b",
        posted_at=None,
        price=None,
        location=None,
        image_urls=[],
        search_name="s",
    )
    out = with_source_prefix(raw, src.source_id)
    assert out.listing_id == "njuskalo:48796313"
    assert out.source == "njuskalo"


def test_registry_contains_kupujemprodajem():
    assert "kupujemprodajem" in LISTING_SOURCES
    assert get_listing_source("kupujemprodajem") is LISTING_SOURCES["kupujemprodajem"]
    assert isinstance(LISTING_SOURCES["kupujemprodajem"], KupujemProdajemSource)

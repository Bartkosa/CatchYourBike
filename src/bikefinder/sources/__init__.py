from __future__ import annotations

from bikefinder.sources.base import ListingSource
from bikefinder.sources.bolha import BolhaSource
from bikefinder.sources.buycycle import BuycycleSource
from bikefinder.sources.ebay import EbaySource
from bikefinder.sources.facebook import FacebookSource
from bikefinder.sources.jofogas import JofogasSource
from bikefinder.sources.kleinanzeigen import KleinanzeigenSource
from bikefinder.sources.kupujemprodajem import KupujemProdajemSource
from bikefinder.sources.njuskalo import NjuskaloSource
from bikefinder.sources.subito import SubitoSource
from bikefinder.sources.wallapop import WallapopSource
from bikefinder.sources.willhaben import WillhabenSource

LISTING_SOURCES: dict[str, ListingSource] = {
    SubitoSource.source_id: SubitoSource(),
    WallapopSource.source_id: WallapopSource(),
    EbaySource.source_id: EbaySource(),
    KleinanzeigenSource.source_id: KleinanzeigenSource(),
    WillhabenSource.source_id: WillhabenSource(),
    BolhaSource.source_id: BolhaSource(),
    NjuskaloSource.source_id: NjuskaloSource(),
    JofogasSource.source_id: JofogasSource(),
    BuycycleSource.source_id: BuycycleSource(),
    FacebookSource.source_id: FacebookSource(),
    KupujemProdajemSource.source_id: KupujemProdajemSource(),
}


def get_listing_source(source_id: str) -> ListingSource:
    key = (source_id or SubitoSource.source_id).strip().lower()
    try:
        return LISTING_SOURCES[key]
    except KeyError:
        known = ", ".join(sorted(LISTING_SOURCES))
        raise ValueError(f"Unknown listing source {source_id!r}. Known: {known}") from None


def register_listing_source(src: ListingSource) -> None:
    """Call from a site plugin module at import time to add a new marketplace."""
    sid = src.source_id.strip().lower()
    LISTING_SOURCES[sid] = src

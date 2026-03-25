from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bikefinder.sources.kleinanzeigen import KleinanzeigenSource
from bikefinder.sources.kleinanzeigen.serp_html import listings_from_search_html
from bikefinder.sources.kleinanzeigen.urls import build_serp_url

_BERLIN = ZoneInfo("Europe/Berlin")

_MINIMAL_ADITEM = """
<article class="aditem" data-adid="3358914929"
         data-href="/s-anzeige/test-bike/3358914929-217-5820">
    <div class="aditem-image">
        <script type="application/ld+json">
            {"contentUrl":"https://img.kleinanzeigen.de/api/v1/prod-ads/images/x.jpg?rule=$_59.AUTO","@type":"ImageObject"}
        </script>
    </div>
    <div class="aditem-main">
        <div class="aditem-main--top">
            <div class="aditem-main--top--left">
                <i class="icon"></i> 84378 Teststadt
            </div>
            <div class="aditem-main--top--right">
                <i class="icon icon-calendar-open"></i>
                Heute, 14:23
            </div>
        </div>
        <div class="aditem-main--middle">
            <h2><a class="ellipsis" href="/s-anzeige/test-bike/3358914929-217-5820">Test Bike Title</a></h2>
            <p class="aditem-main--middle--description">Short body text.</p>
            <div class="aditem-main--middle--price-shipping">
                <p class="aditem-main--middle--price-shipping--price">350 € VB</p>
            </div>
        </div>
    </div>
</article>
"""

_BREADCRUMB = (
    '<span class="breadcrump-summary">1 - 25 von 253.544 Ergebnissen in Deutschland</span>'
)


def test_listings_from_search_html_minimal():
    now = datetime(2026, 3, 21, 16, 0, tzinfo=_BERLIN)
    html = _MINIMAL_ADITEM + _BREADCRUMB + '<link rel="next" href="/s-fahrraeder/preis:150:1200/seite:2/c217"/>'
    listings, total_results, total_pages = listings_from_search_html(
        html, "kz_test", now=now
    )
    assert total_results == 253_544
    assert total_pages >= 1000
    assert len(listings) == 1
    L = listings[0]
    assert L.listing_id == "3358914929"
    assert L.title == "Test Bike Title"
    assert L.body == "Short body text."
    assert L.price == "350 € VB"
    assert L.location == "84378 Teststadt"
    assert L.url == "https://www.kleinanzeigen.de/s-anzeige/test-bike/3358914929-217-5820"
    assert L.image_urls and "kleinanzeigen.de" in L.image_urls[0]
    assert L.posted_at and "2026-03-21T14:23" in L.posted_at


def test_build_serp_url():
    base = "https://www.kleinanzeigen.de/s-fahrraeder/c217"
    assert build_serp_url(base, price_min_eur=150, price_max_eur=1200, page_1based=1) == (
        "https://www.kleinanzeigen.de/s-fahrraeder/preis:150:1200/c217"
    )
    assert build_serp_url(base, price_min_eur=150, price_max_eur=1200, page_1based=2) == (
        "https://www.kleinanzeigen.de/s-fahrraeder/preis:150:1200/seite:2/c217"
    )

    # Category constraints can be appended with `+` (e.g. rennrad filter).
    base_plus = "https://www.kleinanzeigen.de/s-fahrraeder/seite:3/c217+fahrraeder.type_s:rennrad"
    assert build_serp_url(
        base_plus,
        price_min_eur=150,
        price_max_eur=1200,
        page_1based=1,
    ) == (
        "https://www.kleinanzeigen.de/s-fahrraeder/preis:150:1200/c217+fahrraeder.type_s:rennrad"
    )
    assert build_serp_url(
        base_plus,
        price_min_eur=150,
        price_max_eur=1200,
        page_1based=2,
    ) == (
        "https://www.kleinanzeigen.de/s-fahrraeder/preis:150:1200/seite:2/c217+fahrraeder.type_s:rennrad"
    )


def test_kleinanzeigen_parse_posted_at_iso():
    src = KleinanzeigenSource()
    dt = src.parse_posted_at("2026-03-21T14:23:00+01:00")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 3
    assert dt.day == 21

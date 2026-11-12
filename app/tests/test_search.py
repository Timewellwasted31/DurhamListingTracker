"""Tests for the auto-search module."""

from listingtracker.profiles import BuyerProfile
from listingtracker.search import build_search_urls, classify_listing, extract_listing_urls_from_html


def test_build_search_urls_produces_results():
    p = BuyerProfile(cities=["Oshawa", "Pickering"], price_ceiling=450_000)
    urls = build_search_urls(p)
    assert len(urls) > 0
    sites = {u["site"] for u in urls}
    assert "strata.ca" in sites


def test_build_search_urls_uses_buffer():
    p = BuyerProfile(
        cities=["Oshawa"],
        price_ceiling=400_000,
        price_buffer_pct=10.0,
        show_buffer_listings=True,
    )
    urls = build_search_urls(p)
    strata_urls = [u for u in urls if u["site"] == "strata.ca"]
    # condos-for-sale + townhouses-for-sale (default property_types includes townhouse)
    assert len(strata_urls) == 2
    condo_url = [u for u in strata_urls if "condos-for-sale" in u["url"]][0]
    assert "440000" in condo_url["url"]
    town_url = [u for u in strata_urls if "townhouses-for-sale" in u["url"]][0]
    assert "440000" in town_url["url"]


def test_property_types_filter_condo_only_sites():
    """When profile only wants townhouses, condo-only sites are skipped."""
    p = BuyerProfile(
        cities=["Oshawa"],
        price_ceiling=400_000,
        property_types=["townhouse"],
    )
    urls = build_search_urls(p)
    sites = {u["site"] for u in urls}
    # Condo-only sites should be excluded
    assert "condos.ca" not in sites
    assert "condonow.com" not in sites
    assert "condominiums.ca" not in sites
    # strata.ca should only have townhouses URL, no condos
    strata_urls = [u for u in urls if u["site"] == "strata.ca"]
    assert len(strata_urls) == 1
    assert "townhouses-for-sale" in strata_urls[0]["url"]
    # Generic sites (realtor.ca, zolo.ca, etc.) still included
    assert "realtor.ca" in sites
    assert "zolo.ca" in sites


def test_property_types_condo_only_includes_condo_sites():
    """When profile only wants condos, condo-only sites are included but no townhouse URLs."""
    p = BuyerProfile(
        cities=["Oshawa"],
        price_ceiling=400_000,
        property_types=["condo"],
    )
    urls = build_search_urls(p)
    sites = {u["site"] for u in urls}
    assert "condos.ca" in sites
    assert "condonow.com" in sites
    assert "condominiums.ca" in sites
    strata_urls = [u for u in urls if u["site"] == "strata.ca"]
    assert len(strata_urls) == 1
    assert "condos-for-sale" in strata_urls[0]["url"]


def test_classify_within():
    p = BuyerProfile(
        price_ceiling=450_000,
        monthly_allin_ceiling=2_100,
        fee_per_sqft_reject=1.00,
        price_buffer_pct=10.0,
        monthly_buffer_pct=10.0,
        fee_buffer_pct=15.0,
    )
    assert classify_listing(400_000, 1_800, 0.70, p) == "within"


def test_classify_buffer():
    p = BuyerProfile(
        price_ceiling=450_000,
        monthly_allin_ceiling=2_100,
        fee_per_sqft_reject=1.00,
        price_buffer_pct=10.0,
        monthly_buffer_pct=10.0,
        fee_buffer_pct=15.0,
    )
    assert classify_listing(470_000, 1_800, 0.70, p) == "buffer"


def test_classify_over():
    p = BuyerProfile(
        price_ceiling=450_000,
        monthly_allin_ceiling=2_100,
        fee_per_sqft_reject=1.00,
        price_buffer_pct=10.0,
        monthly_buffer_pct=10.0,
        fee_buffer_pct=15.0,
    )
    assert classify_listing(600_000, 1_800, 0.70, p) == "over"


def test_classify_fee_buffer():
    p = BuyerProfile(
        price_ceiling=450_000,
        monthly_allin_ceiling=2_100,
        fee_per_sqft_reject=1.00,
        price_buffer_pct=10.0,
        monthly_buffer_pct=10.0,
        fee_buffer_pct=15.0,
    )
    assert classify_listing(400_000, 1_800, 1.10, p) == "buffer"


def test_classify_fee_over():
    p = BuyerProfile(
        price_ceiling=450_000,
        monthly_allin_ceiling=2_100,
        fee_per_sqft_reject=1.00,
        price_buffer_pct=10.0,
        monthly_buffer_pct=10.0,
        fee_buffer_pct=15.0,
    )
    assert classify_listing(400_000, 1_800, 1.20, p) == "over"


class TestExtractListingUrls:
    def test_strata_absolute_url(self):
        html = '<a href="https://strata.ca/oshawa/some-building/3-unit-622">Link</a>'
        urls = extract_listing_urls_from_html(html, "strata.ca")
        assert "https://strata.ca/oshawa/some-building/3-unit-622" in urls

    def test_strata_relative_url(self):
        html = '<a href="/oshawa/2550-simcoe-st-north-uc-tower/3-unit-622">Link</a>'
        urls = extract_listing_urls_from_html(html, "strata.ca")
        assert "https://strata.ca/oshawa/2550-simcoe-st-north-uc-tower/3-unit-622" in urls

    def test_strata_relative_url_excludes_search_pages(self):
        html = '<a href="/oshawa/condos-for-sale?maxPrice=500000">Search</a>'
        urls = extract_listing_urls_from_html(html, "strata.ca")
        assert len(urls) == 0

    def test_strata_relative_url_excludes_next_internal(self):
        html = '<a href="/_next/static/chunks/123abc.js">JS</a>'
        urls = extract_listing_urls_from_html(html, "strata.ca")
        assert len(urls) == 0

    def test_strata_rsc_quoted_url(self):
        html = '"https://strata.ca/oshawa/building-name/123-unit-abc"'
        urls = extract_listing_urls_from_html(html, "strata.ca")
        assert "https://strata.ca/oshawa/building-name/123-unit-abc" in urls

    def test_strata_relative_multiple_listings(self):
        html = (
            '<a href="/oshawa/building-a/1-unit-100">A</a>'
            '<a href="/oshawa/building-b/2-unit-200">B</a>'
            '<a href="/pickering/building-c/3-unit-300">C</a>'
        )
        urls = extract_listing_urls_from_html(html, "strata.ca")
        assert len(urls) == 3


class TestExtractZoloUrls:
    def test_zolo_absolute_url(self):
        html = '<a href="https://www.zolo.ca/oshawa-real-estate/2550-simcoe-street-north/502">Link</a>'
        urls = extract_listing_urls_from_html(html, "zolo.ca")
        assert "https://www.zolo.ca/oshawa-real-estate/2550-simcoe-street-north/502" in urls

    def test_zolo_relative_url(self):
        html = '<a href="/oshawa-real-estate/2550-simcoe-street-north/502">Link</a>'
        urls = extract_listing_urls_from_html(html, "zolo.ca")
        assert "https://www.zolo.ca/oshawa-real-estate/2550-simcoe-street-north/502" in urls

    def test_zolo_excludes_search_pages(self):
        html = '<a href="/oshawa-real-estate/condos-for-sale">Search</a>'
        urls = extract_listing_urls_from_html(html, "zolo.ca")
        assert len(urls) == 0

"""Comprehensive functional tests — profiles, search, property types, classification, sorting."""

import pytest
from listingtracker.profiles import BuyerProfile, save_profile, load_profile, delete_profile
from listingtracker.search import build_search_urls, classify_listing, check_deal_breakers
from listingtracker.finance import (
    monthly_payment, FinanceConfig, qualifies,
    price_per_sqft, fee_per_sqft, ontario_land_transfer_tax,
    first_time_buyer_rebate,
)
from listingtracker.models import Listing, Snapshot, Status
from listingtracker.diffing import diff_snapshots, sort_changes


# ---------------------------------------------------------------------------
# Profile CRUD
# ---------------------------------------------------------------------------

class TestProfileCRUD:
    def test_create_save_load_delete(self, tmp_path, monkeypatch):
        monkeypatch.setattr("listingtracker.profiles.PROFILES_DIR", tmp_path)
        p = BuyerProfile(
            name="Test Profile",
            cities=["Oshawa"],
            price_ceiling=300_000,
            property_types=["condo"],
        )
        save_profile(p)
        loaded = load_profile("Test Profile")
        assert loaded is not None
        assert loaded.name == "Test Profile"
        assert loaded.cities == ["Oshawa"]
        assert loaded.price_ceiling == 300_000
        assert loaded.property_types == ["condo"]
        assert delete_profile("Test Profile")
        assert load_profile("Test Profile") is None

    def test_profile_defaults(self):
        p = BuyerProfile()
        assert p.property_types == ["condo", "townhouse"]
        assert p.min_beds == 2
        assert p.min_baths == 1
        assert p.parking_required is True
        assert p.fee_per_sqft_reject == 1.00
        assert p.price_buffer_pct == 10.0

    def test_buffered_values(self):
        p = BuyerProfile(
            price_ceiling=400_000,
            price_buffer_pct=10.0,
            monthly_allin_ceiling=2000,
            monthly_buffer_pct=10.0,
            fee_per_sqft_reject=1.00,
            fee_buffer_pct=15.0,
        )
        assert p.buffered_price_ceiling() == pytest.approx(440_000)
        assert p.buffered_monthly_ceiling() == pytest.approx(2200)
        assert p.buffered_fee_reject() == pytest.approx(1.15)

    def test_search_cities_default(self):
        p = BuyerProfile(cities=[])
        defaults = p.search_cities()
        assert "oshawa" in defaults
        assert "pickering" in defaults

    def test_search_cities_custom(self):
        p = BuyerProfile(cities=["Whitby", "Ajax"])
        assert p.search_cities() == ["Whitby", "Ajax"]


# ---------------------------------------------------------------------------
# Property Type Filtering in Search
# ---------------------------------------------------------------------------

class TestPropertyTypeSearch:
    def test_condo_only_profile(self):
        """Profile wanting only condos: condo-specific sites included, no townhouse URLs."""
        p = BuyerProfile(cities=["Oshawa"], price_ceiling=350_000, property_types=["condo"])
        urls = build_search_urls(p)
        sites = {u["site"] for u in urls}
        assert "condos.ca" in sites
        assert "condonow.com" in sites
        assert "condominiums.ca" in sites
        strata = [u for u in urls if u["site"] == "strata.ca"]
        assert len(strata) == 1
        assert "condos-for-sale" in strata[0]["url"]
        assert not any("townhouses-for-sale" in u["url"] for u in urls)

    def test_townhouse_only_profile(self):
        """Profile wanting only townhouses: condo-only sites excluded."""
        p = BuyerProfile(cities=["Oshawa"], price_ceiling=350_000, property_types=["townhouse"])
        urls = build_search_urls(p)
        sites = {u["site"] for u in urls}
        assert "condos.ca" not in sites
        assert "condonow.com" not in sites
        assert "condominiums.ca" not in sites
        strata = [u for u in urls if u["site"] == "strata.ca"]
        assert len(strata) == 1
        assert "townhouses-for-sale" in strata[0]["url"]

    def test_both_property_types(self):
        """Default profile gets both condo and townhouse URLs."""
        p = BuyerProfile(cities=["Oshawa"], price_ceiling=350_000,
                         property_types=["condo", "townhouse"])
        urls = build_search_urls(p)
        sites = {u["site"] for u in urls}
        assert "condos.ca" in sites
        strata = [u for u in urls if u["site"] == "strata.ca"]
        assert len(strata) == 2
        strata_paths = {u["url"].split("/")[4] if len(u["url"].split("/")) > 4 else "" for u in strata}
        assert "condos-for-sale" in strata_paths or any("condos-for-sale" in u["url"] for u in strata)
        assert any("townhouses-for-sale" in u["url"] for u in strata)

    def test_empty_property_types_defaults_to_all(self):
        """Empty property_types includes everything (backward compat)."""
        p = BuyerProfile(cities=["Oshawa"], price_ceiling=350_000, property_types=[])
        urls = build_search_urls(p)
        sites = {u["site"] for u in urls}
        assert "condos.ca" in sites
        assert "realtor.ca" in sites

    def test_generic_sites_always_included(self):
        """realtor.ca, zolo.ca, housesigma.com, wahi.com, rew.ca are always included."""
        for prop_type in [["condo"], ["townhouse"], ["condo", "townhouse"]]:
            p = BuyerProfile(cities=["Oshawa"], price_ceiling=350_000, property_types=prop_type)
            urls = build_search_urls(p)
            sites = {u["site"] for u in urls}
            assert "realtor.ca" in sites, f"realtor.ca missing for {prop_type}"
            assert "zolo.ca" in sites, f"zolo.ca missing for {prop_type}"
            assert "housesigma.com" in sites, f"housesigma.com missing for {prop_type}"


# ---------------------------------------------------------------------------
# Search URL Construction
# ---------------------------------------------------------------------------

class TestSearchURLs:
    def test_price_ceiling_in_urls(self):
        p = BuyerProfile(cities=["Oshawa"], price_ceiling=300_000,
                         show_buffer_listings=False)
        urls = build_search_urls(p)
        strata = [u for u in urls if u["site"] == "strata.ca"]
        assert len(strata) > 0
        assert "300000" in strata[0]["url"]

    def test_buffer_price_in_urls(self):
        p = BuyerProfile(
            cities=["Oshawa"],
            price_ceiling=300_000,
            price_buffer_pct=10.0,
            show_buffer_listings=True,
        )
        urls = build_search_urls(p)
        strata = [u for u in urls if u["site"] == "strata.ca"]
        assert "330000" in strata[0]["url"]

    def test_multiple_cities(self):
        p = BuyerProfile(cities=["Oshawa", "Whitby", "Ajax"], price_ceiling=400_000)
        urls = build_search_urls(p)
        sites_per_city = {}
        for u in urls:
            url = u["url"].lower()
            for city in ["oshawa", "whitby", "ajax"]:
                if city in url:
                    sites_per_city.setdefault(city, set()).add(u["site"])
        assert len(sites_per_city) >= 2

    def test_single_city_limited_scope(self):
        """One city should produce a manageable number of URLs."""
        p = BuyerProfile(cities=["Oshawa"], price_ceiling=400_000, property_types=["condo"])
        urls = build_search_urls(p)
        assert len(urls) > 0
        assert len(urls) < 50


# ---------------------------------------------------------------------------
# Classification / Qualification
# ---------------------------------------------------------------------------

class TestClassification:
    def test_within_all_criteria(self):
        p = BuyerProfile(
            price_ceiling=450_000,
            monthly_allin_ceiling=2_100,
            fee_per_sqft_reject=1.00,
            price_buffer_pct=10.0,
            monthly_buffer_pct=10.0,
            fee_buffer_pct=15.0,
        )
        assert classify_listing(400_000, 1_800, 0.70, p) == "within"

    def test_price_in_buffer(self):
        p = BuyerProfile(
            price_ceiling=450_000,
            monthly_allin_ceiling=2_100,
            fee_per_sqft_reject=1.00,
            price_buffer_pct=10.0,
            monthly_buffer_pct=10.0,
            fee_buffer_pct=15.0,
        )
        assert classify_listing(480_000, 1_800, 0.70, p) == "buffer"

    def test_price_over(self):
        p = BuyerProfile(
            price_ceiling=450_000,
            monthly_allin_ceiling=2_100,
            fee_per_sqft_reject=1.00,
            price_buffer_pct=10.0,
            monthly_buffer_pct=10.0,
            fee_buffer_pct=15.0,
        )
        assert classify_listing(600_000, 1_800, 0.70, p) == "over"

    def test_monthly_in_buffer(self):
        p = BuyerProfile(
            price_ceiling=450_000,
            monthly_allin_ceiling=2_100,
            fee_per_sqft_reject=1.00,
            price_buffer_pct=10.0,
            monthly_buffer_pct=10.0,
            fee_buffer_pct=15.0,
        )
        assert classify_listing(400_000, 2_200, 0.70, p) == "buffer"

    def test_fee_over(self):
        p = BuyerProfile(
            price_ceiling=450_000,
            monthly_allin_ceiling=2_100,
            fee_per_sqft_reject=1.00,
            price_buffer_pct=10.0,
            monthly_buffer_pct=10.0,
            fee_buffer_pct=15.0,
        )
        assert classify_listing(400_000, 1_800, 1.20, p) == "over"

    def test_none_values_dont_crash(self):
        p = BuyerProfile(
            price_ceiling=450_000,
            monthly_allin_ceiling=2_100,
            fee_per_sqft_reject=1.00,
        )
        result = classify_listing(None, None, None, p)
        assert result in ("within", "buffer", "over")


# ---------------------------------------------------------------------------
# Finance Calculations
# ---------------------------------------------------------------------------

class TestFinance:
    def test_mortgage_calculation(self):
        payment = monthly_payment(300_000, 0.04, 25)
        assert 1500 < payment < 1600

    def test_land_transfer_tax(self):
        tax = ontario_land_transfer_tax(400_000)
        assert tax > 0
        assert tax < 10_000

    def test_first_time_buyer_rebate(self):
        ltt = ontario_land_transfer_tax(400_000)
        rebate = first_time_buyer_rebate(ltt)
        assert rebate > 0
        assert rebate <= 4_000
        assert ltt - rebate < ltt

    def test_price_per_sqft(self):
        assert price_per_sqft(400_000, 800, 800) == 500.0
        assert price_per_sqft(400_000, 600, 1000) == 500.0
        assert price_per_sqft(0, 800, 800) is None
        assert price_per_sqft(400_000, None, None) is None

    def test_fee_per_sqft(self):
        assert fee_per_sqft(800, 800, 800) == 1.0
        assert fee_per_sqft(400, 800, 800) == 0.5
        assert fee_per_sqft(0, 800, 800) is None

    def test_qualifies_within_budget(self):
        cfg = FinanceConfig(
            down_payment_available=200_000,
            annual_income=80_000,
            monthly_debt_obligations=0,
            contract_rate=0.04,
            amortization_years=25,
            gds_limit=0.39,
            tds_limit=0.44,
            lender_heat_estimate=100,
            hydro_estimate=90,
            insurance_estimate=40,
            first_time_buyer=False,
        )
        result = qualifies(300_000, 500, 3000, "", cfg)
        assert result is True

    def test_qualifies_fails_over_budget(self):
        cfg = FinanceConfig(
            down_payment_available=20_000,
            annual_income=50_000,
            monthly_debt_obligations=500,
            contract_rate=0.06,
            amortization_years=25,
        )
        result = qualifies(800_000, 800, 5000, "", cfg)
        assert result is False


# ---------------------------------------------------------------------------
# Diffing / Change Detection
# ---------------------------------------------------------------------------

class TestDiffing:
    def _listing(self):
        return Listing(id=1, url="https://test.ca/123", site="test.ca",
                       address="123 Test St", city="Oshawa")

    def test_first_snapshot_no_changes_for_sale(self):
        listing = self._listing()
        snap = Snapshot(listing_id=1, status=Status.FOR_SALE, list_price=300_000)
        changes = diff_snapshots(listing, None, snap)
        assert len(changes) == 0

    def test_first_snapshot_non_sale_status(self):
        listing = self._listing()
        snap = Snapshot(listing_id=1, status=Status.SOLD, list_price=300_000)
        changes = diff_snapshots(listing, None, snap)
        assert len(changes) == 1
        assert changes[0].field == "status"

    def test_price_change_detected(self):
        listing = self._listing()
        old = Snapshot(listing_id=1, status=Status.FOR_SALE, list_price=300_000)
        new = Snapshot(listing_id=1, status=Status.FOR_SALE, list_price=280_000)
        changes = diff_snapshots(listing, old, new)
        price_changes = [c for c in changes if c.field == "list_price"]
        assert len(price_changes) == 1
        assert "280,000" in price_changes[0].description

    def test_status_change_severity(self):
        listing = self._listing()
        old = Snapshot(listing_id=1, status=Status.FOR_SALE, list_price=300_000)
        new = Snapshot(listing_id=1, status=Status.SOLD, list_price=300_000)
        changes = diff_snapshots(listing, old, new)
        status_changes = [c for c in changes if c.field == "status"]
        assert len(status_changes) == 1
        assert status_changes[0].severity == "critical"

    def test_no_change_no_diff(self):
        listing = self._listing()
        old = Snapshot(listing_id=1, status=Status.FOR_SALE, list_price=300_000,
                       maintenance_fee=500, beds=2, baths=1)
        new = Snapshot(listing_id=1, status=Status.FOR_SALE, list_price=300_000,
                       maintenance_fee=500, beds=2, baths=1)
        changes = diff_snapshots(listing, old, new)
        assert len(changes) == 0

    def test_sort_changes_by_severity(self):
        listing = self._listing()
        old = Snapshot(listing_id=1, status=Status.FOR_SALE, list_price=300_000,
                       maintenance_fee=500)
        new = Snapshot(listing_id=1, status=Status.SOLD, list_price=280_000,
                       maintenance_fee=600)
        changes = diff_snapshots(listing, old, new)
        sorted_ch = sort_changes(changes)
        if len(sorted_ch) >= 2:
            severities = [c.severity for c in sorted_ch]
            priority = {"critical": 0, "warning": 1, "info": 2}
            for i in range(len(severities) - 1):
                assert priority.get(severities[i], 3) <= priority.get(severities[i+1], 3)


# ---------------------------------------------------------------------------
# Deal Breakers
# ---------------------------------------------------------------------------

class TestDealBreakers:
    def test_fee_too_high_triggers(self):
        p = BuyerProfile(fee_per_sqft_reject=1.00)
        snap = Snapshot(listing_id=1, maintenance_fee=1000, sqft_min=800, sqft_max=800)
        breakers = check_deal_breakers(snap, p)
        assert any("fee" in b.lower() for b in breakers)

    def test_fee_ok_no_trigger(self):
        p = BuyerProfile(fee_per_sqft_reject=1.00)
        snap = Snapshot(listing_id=1, maintenance_fee=400, sqft_min=800, sqft_max=800)
        breakers = check_deal_breakers(snap, p)
        assert not any("fee" in b.lower() for b in breakers)

    def test_no_data_no_crash(self):
        p = BuyerProfile()
        snap = Snapshot(listing_id=1)
        breakers = check_deal_breakers(snap, p)
        assert isinstance(breakers, list)


# ---------------------------------------------------------------------------
# Power of Sale Detection
# ---------------------------------------------------------------------------

class TestPowerOfSale:
    def test_detect_power_of_sale_keywords(self):
        from listingtracker.adapters.base import SiteAdapter
        cases = [
            ("This is a power of sale property", True),
            ("Bank sale condition applies", True),
            ("Bank-owned unit for sale", True),
            ("Court-ordered sale proceeding", True),
            ("Mortgagee sale in effect", True),
            ("Beautiful condo for sale in Oshawa", False),
            ("Great investment opportunity!", False),
        ]
        for html, expected in cases:
            assert SiteAdapter._detect_power_of_sale(html) == expected, f"Failed for: {html}"


# ---------------------------------------------------------------------------
# Search Site Selection
# ---------------------------------------------------------------------------

class TestSearchSiteSelection:
    def test_all_sites_by_default(self):
        """Empty search_sites means all sites are used."""
        p = BuyerProfile(cities=["Oshawa"], price_ceiling=400_000, search_sites=[])
        urls = build_search_urls(p)
        sites = {u["site"] for u in urls}
        assert len(sites) >= 5

    def test_limited_sites_via_parameter(self):
        """Only specified sites should appear when passed to build_search_urls."""
        p = BuyerProfile(cities=["Oshawa"], price_ceiling=400_000)
        urls = build_search_urls(p, sites=["strata.ca", "realtor.ca"])
        sites = {u["site"] for u in urls}
        assert sites == {"strata.ca", "realtor.ca"}

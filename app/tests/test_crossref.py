"""Tests for the cross-referencing module."""

from listingtracker.models import Listing, Snapshot, Status
from listingtracker.crossref import (
    find_cross_ref_groups,
    compare_group,
    build_cross_ref_results,
    get_cross_ref_for_listing,
    build_enrichment_search_urls,
    build_enrichment_from_parsed,
    merge_from_cross_ref,
    _normalize_address,
    _normalize_unit,
    _values_differ,
    CrossRefResult,
    EnrichmentItem,
)


def _make_listing(id, site, address="", unit=None, mls=None, city="Pickering"):
    return Listing(
        id=id, url=f"https://{site}/listing-{id}", site=site,
        address=address, unit=unit, mls_number=mls, city=city,
    )


def _make_snapshot(listing_id, price=None, beds=None, baths=None, status=Status.FOR_SALE):
    return Snapshot(
        id=listing_id * 100, listing_id=listing_id,
        list_price=price, beds=beds, baths=baths, status=status,
    )


class TestNormalization:
    def test_normalize_address_basic(self):
        assert _normalize_address("1210 Radom St") == "1210 radom st"

    def test_normalize_address_with_street_suffix(self):
        a = _normalize_address("100 Main Street")
        b = _normalize_address("100 Main St")
        assert a == b

    def test_normalize_address_strips_punctuation(self):
        assert _normalize_address("1210 Radom St.") == "1210 radom st"

    def test_normalize_unit(self):
        assert _normalize_unit("506") == "506"
        assert _normalize_unit("#506") == "506"
        assert _normalize_unit("Unit 506") == "unit506"
        assert _normalize_unit(None) == ""


class TestFindGroups:
    def test_mls_match(self):
        listings = [
            _make_listing(1, "strata.ca", "1210 Radom St", "506", mls="E9876543"),
            _make_listing(2, "realtor.ca", "1210 Radom Street", "506", mls="E9876543"),
            _make_listing(3, "strata.ca", "99 Other Ave", "100"),
        ]
        snaps = {l.id: None for l in listings}
        groups = find_cross_ref_groups(listings, snaps)
        assert len(groups) == 1
        assert sorted(groups[0]) == [1, 2]

    def test_address_match_different_sites(self):
        listings = [
            _make_listing(1, "strata.ca", "1210 Radom St", "506"),
            _make_listing(2, "zolo.ca", "1210 Radom St", "506"),
        ]
        snaps = {l.id: None for l in listings}
        groups = find_cross_ref_groups(listings, snaps)
        assert len(groups) == 1

    def test_same_site_no_cross_ref(self):
        listings = [
            _make_listing(1, "strata.ca", "1210 Radom St", "506"),
            _make_listing(2, "strata.ca", "1210 Radom St", "506"),
        ]
        snaps = {l.id: None for l in listings}
        groups = find_cross_ref_groups(listings, snaps)
        assert len(groups) == 0

    def test_no_match(self):
        listings = [
            _make_listing(1, "strata.ca", "1210 Radom St", "506"),
            _make_listing(2, "zolo.ca", "99 Other Ave", "100"),
        ]
        snaps = {l.id: None for l in listings}
        groups = find_cross_ref_groups(listings, snaps)
        assert len(groups) == 0

    def test_three_sites_one_group(self):
        listings = [
            _make_listing(1, "strata.ca", "1210 Radom St", "506", mls="E9876543"),
            _make_listing(2, "zolo.ca", "1210 Radom St", "506", mls="E9876543"),
            _make_listing(3, "realtor.ca", "1210 Radom St", "506", mls="E9876543"),
        ]
        snaps = {l.id: None for l in listings}
        groups = find_cross_ref_groups(listings, snaps)
        assert len(groups) == 1
        assert sorted(groups[0]) == [1, 2, 3]


class TestCompareGroup:
    def test_all_agree(self):
        listings = [
            _make_listing(1, "strata.ca", "1210 Radom St", "506"),
            _make_listing(2, "zolo.ca", "1210 Radom St", "506"),
        ]
        snaps = {
            1: _make_snapshot(1, price=449900, beds=2, baths=1),
            2: _make_snapshot(2, price=449900, beds=2, baths=1),
        }
        comparisons = compare_group(listings, snaps)
        price_comp = next(c for c in comparisons if c.field == "list_price")
        assert price_comp.agreed is True
        assert price_comp.consensus == 449900

        beds_comp = next(c for c in comparisons if c.field == "beds")
        assert beds_comp.agreed is True

    def test_price_disagree(self):
        listings = [
            _make_listing(1, "strata.ca", "1210 Radom St", "506"),
            _make_listing(2, "zolo.ca", "1210 Radom St", "506"),
        ]
        snaps = {
            1: _make_snapshot(1, price=449900, beds=2),
            2: _make_snapshot(2, price=459900, beds=2),
        }
        comparisons = compare_group(listings, snaps)
        price_comp = next(c for c in comparisons if c.field == "list_price")
        assert price_comp.agreed is False
        assert "strata.ca" in price_comp.values
        assert "zolo.ca" in price_comp.values

    def test_missing_data_still_compares(self):
        listings = [
            _make_listing(1, "strata.ca", "1210 Radom St", "506"),
            _make_listing(2, "zolo.ca", "1210 Radom St", "506"),
        ]
        snaps = {
            1: _make_snapshot(1, price=449900, beds=2, baths=1),
            2: _make_snapshot(2, price=449900, beds=None, baths=1),
        }
        comparisons = compare_group(listings, snaps)
        beds_comp = next(c for c in comparisons if c.field == "beds")
        assert len(beds_comp.values) == 1
        assert beds_comp.agreed is True


class TestBuildResults:
    def test_full_pipeline(self):
        listings = [
            _make_listing(1, "strata.ca", "1210 Radom St", "506", mls="E9876543"),
            _make_listing(2, "zolo.ca", "1210 Radom St", "506", mls="E9876543"),
            _make_listing(3, "strata.ca", "99 Other Ave", "100"),
        ]
        snaps = {
            1: _make_snapshot(1, price=449900, beds=2),
            2: _make_snapshot(2, price=449900, beds=2),
            3: _make_snapshot(3, price=300000, beds=1),
        }
        results = build_cross_ref_results(listings, snaps)
        assert len(results) == 1
        assert "E9876543" in results[0].match_reason

    def test_get_for_listing(self):
        listings = [
            _make_listing(1, "strata.ca", "1210 Radom St", "506", mls="E9876543"),
            _make_listing(2, "zolo.ca", "1210 Radom St", "506", mls="E9876543"),
        ]
        snaps = {
            1: _make_snapshot(1, price=449900),
            2: _make_snapshot(2, price=449900),
        }
        results = build_cross_ref_results(listings, snaps)
        xref = get_cross_ref_for_listing(1, results)
        assert xref is not None
        assert len(xref.listings) == 2

        assert get_cross_ref_for_listing(999, results) is None


class TestEnrichmentSearchUrls:
    def test_generates_urls_for_other_sites(self):
        listing = _make_listing(1, "strata.ca", "1210 Radom St", "506", mls="E9876543")
        urls = build_enrichment_search_urls(listing)
        sites = [u["site"] for u in urls]
        assert "strata.ca" not in sites
        assert "housesigma.com" in sites
        assert "zolo.ca" in sites

    def test_no_address_no_mls_returns_empty(self):
        listing = _make_listing(1, "strata.ca", "", None)
        urls = build_enrichment_search_urls(listing)
        assert urls == []

    def test_excludes_source_site(self):
        listing = _make_listing(1, "housesigma.com", "1210 Radom St", "506")
        urls = build_enrichment_search_urls(listing)
        sites = [u["site"] for u in urls]
        assert "housesigma.com" not in sites

    def test_uses_mls_as_query(self):
        listing = _make_listing(1, "strata.ca", "1210 Radom St", "506", mls="E9876543")
        urls = build_enrichment_search_urls(listing)
        for u in urls:
            assert "E9876543" in u["url"]


class TestEnrichmentFromParsed:
    def test_finds_missing_fields(self):
        snap = _make_snapshot(1, price=449900, beds=2, baths=1)
        parsed = {"taxes_annual": 3200, "parking": "1"}
        items = build_enrichment_from_parsed(1, snap, "zolo.ca", "https://zolo.ca/x", parsed)
        fields = {i.field for i in items}
        assert "taxes_annual" in fields
        assert "parking" in fields

    def test_finds_differing_fields(self):
        snap = _make_snapshot(1, price=449900, beds=2)
        parsed = {"list_price": 459900, "beds": 2}
        items = build_enrichment_from_parsed(1, snap, "zolo.ca", "https://zolo.ca/x", parsed)
        fields = {i.field for i in items}
        assert "list_price" in fields
        assert "beds" not in fields

    def test_no_parsed_data_returns_empty(self):
        snap = _make_snapshot(1, price=449900)
        items = build_enrichment_from_parsed(1, snap, "zolo.ca", "https://zolo.ca/x", {})
        assert items == []


class TestMergeFromCrossRef:
    def test_fills_missing_from_other_listing(self):
        snap1 = _make_snapshot(1, price=449900, beds=None, baths=None)
        snap2 = _make_snapshot(2, price=449900, beds=2, baths=1)
        listings = [
            _make_listing(1, "strata.ca", "1210 Radom St", "506", mls="E9876543"),
            _make_listing(2, "zolo.ca", "1210 Radom St", "506", mls="E9876543"),
        ]
        xref = CrossRefResult(
            group_id=1, listings=listings,
            snapshots={1: snap1, 2: snap2}, match_reason="MLS# E9876543",
        )
        items = merge_from_cross_ref(1, snap1, xref)
        fields = {i.field for i in items}
        assert "beds" in fields
        assert "baths" in fields

    def test_no_xref_returns_empty(self):
        snap = _make_snapshot(1, price=449900)
        assert merge_from_cross_ref(1, snap, None) == []


class TestValuesDiffer:
    def test_same_numbers(self):
        assert _values_differ(449900, 449900) is False

    def test_close_floats(self):
        assert _values_differ(449900.0, 449900.005) is False

    def test_different_numbers(self):
        assert _values_differ(449900, 459900) is True

    def test_same_strings_case_insensitive(self):
        assert _values_differ("Yes", "yes") is False

    def test_none_handling(self):
        assert _values_differ(None, 5) is True
        assert _values_differ(None, None) is False


class TestSamePropertyCheck:
    """Data from another site only counts when the page is verifiably the same property."""

    def _listing(self, address="43 Taunton Rd E", unit="36", mls=None):
        return Listing(id=1, url="https://strata.ca/x", site="strata.ca",
                       address=address, unit=unit, mls_number=mls, city="Oshawa")

    def test_matching_mls_number_is_enough(self):
        from listingtracker.crossref import is_same_property
        listing = self._listing(mls="E13481982")
        assert is_same_property(listing, {"mls_number": "e13481982", "address": "somewhere else"})
        assert not is_same_property(listing, {"mls_number": "E99999999", "address": "43 Taunton Rd E", "unit": "36"})

    def test_address_and_unit_must_match(self):
        from listingtracker.crossref import is_same_property
        listing = self._listing()
        assert is_same_property(listing, {"address": "43 Taunton Road East", "unit": "36"})
        assert is_same_property(listing, {"address": "#36 - 43 Taunton Rd E, Oshawa"})
        assert is_same_property(listing, {"address": "43 Taunton Rd E Unit 36"})
        assert not is_same_property(listing, {"address": "43 Taunton Rd E", "unit": "12"})   # neighbour
        assert not is_same_property(listing, {"address": "43 Taunton Rd E"})                 # unit unknown
        assert not is_same_property(listing, {"address": "131 Taunton Rd E", "unit": "36"})  # other building
        assert not is_same_property(listing, {"address": "43 Simcoe St N", "unit": "36"})
        assert not is_same_property(listing, {"address": "", "list_price": 1588000})         # a search page

    def test_house_without_unit(self):
        from listingtracker.crossref import is_same_property
        listing = self._listing(address="157 Dooley Cres", unit=None)
        assert is_same_property(listing, {"address": "157 Dooley Crescent, Ajax"})
        assert not is_same_property(listing, {"address": "15 Dooley Crescent"})
        assert not is_same_property(listing, {"address": "157 Dooley Street"})

    def test_candidate_links_that_look_like_the_listing_come_first(self):
        from listingtracker.crossref import rank_candidate_urls
        urls = [
            "https://housesigma.com/web/en/listing/abc123",
            "https://www.zolo.ca/oshawa-real-estate/131-taunton-road-e/201",
            "https://www.zolo.ca/oshawa-real-estate/43-taunton-road-e/36",
            "https://www.zolo.ca/oshawa-real-estate/43-taunton-road-e/12",
            "https://www.zolo.ca/oshawa-real-estate/143-taunton-road-e/36",
        ]
        ranked = rank_candidate_urls(urls, self._listing())
        assert ranked[0] == "https://www.zolo.ca/oshawa-real-estate/43-taunton-road-e/36"
        assert ranked[1] == "https://www.zolo.ca/oshawa-real-estate/43-taunton-road-e/12"

    def test_page_code_is_never_offered_as_a_value(self):
        snap = Snapshot(id=1, listing_id=1, list_price=339999.0, broker="KELLER WILLIAMS EMPOWERED REALTY")
        items = build_enrichment_from_parsed(
            1, snap, "housesigma.com", "https://housesigma.com/x",
            {"broker": '" class="hs-meta">', "parking": "1 space", "locker": '":null,"stories":"1"'},
        )
        assert [(i.field, i.found_value) for i in items] == [("parking", "1 space")]

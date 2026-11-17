"""Price history from the listing sites, and the next-price-cut forecast."""

from datetime import date, datetime

import pytest

from listingtracker import pricehistory as ph
from listingtracker.models import Snapshot, Status


def d(s):
    return date.fromisoformat(s)


def snap(day, price, status=Status.FOR_SALE):
    return Snapshot(listing_id=1, fetched_at=datetime.fromisoformat(day + "T15:00:00"), status=status,
                    list_price=price)


# --- What the sites report ---

def test_strata_matches_the_price_list_to_the_listing_by_mls_number():
    from listingtracker.adapters.strata import StrataAdapter
    rsc = (
        '{"listPrices":[{"price":540000,"priceDate":"2024-02-20"}],"mlsNumber":"E1111111"}'
        '{"listPrices":[{"price":425000,"priceDate":"2026-07-24"},{"price":475000,"priceDate":"2026-06-23"}],'
        '"distributeToInternetPortals":true,"mlsNumber":"E2222222"}'
    )
    assert StrataAdapter._price_history(rsc, "E2222222") == (
        "2026-06-23", [("2026-06-23", 475000.0), ("2026-07-24", 425000.0)])
    assert StrataAdapter._price_history(rsc, "E9999999") == (None, [])


def test_wahi_opening_price_is_added_only_when_it_starts_on_the_list_date():
    from listingtracker.adapters.wahi import WahiAdapter
    listing = {"listDate": "2026-05-01T04:00:00.000Z", "listPrice": "850000", "priceUpdates": [
        {"date": "2026-06-01T04:00:00.000Z", "oldPrice": 900000, "newPrice": 850000},
    ]}
    assert WahiAdapter._price_history(listing) == (
        "2026-05-01", [("2026-05-01", 900000.0), ("2026-06-01", 850000.0)])

    relisted = {"listDate": "2026-07-08T04:00:00.000Z", "listPrice": "799000", "priceUpdates": [
        {"date": "2026-07-08T04:00:00.000Z", "oldPrice": 959000, "newPrice": 799000},
        {"date": "2026-05-22T04:00:00+00:00", "oldPrice": 998000, "newPrice": 899999},
    ]}
    assert WahiAdapter._price_history(relisted)[1] == [("2026-05-22", 899999.0), ("2026-07-08", 799000.0)]

    unchanged = {"listDate": "2026-11-06T05:00:00.000Z", "listPrice": "1099990", "priceUpdates": []}
    assert WahiAdapter._price_history(unchanged) == ("2026-11-06", [("2026-11-06", 1099990.0)])


def test_strata_sales_are_matched_to_this_unit():
    from listingtracker.adapters.strata import StrataAdapter
    rsc = (
        '{"propertyListingState":"SOLD","propertyUnitUid":"31-unit-104","soldDate":"2015-05-11","soldPrice":338800}'
        '{"propertyListingState":"SOLD","propertyUnitUid":"31-unit-105","soldDate":"2019-02-01","soldPrice":410000}'
        '{"propertyListingState":"TERMINATED","propertyUnitUid":"31-unit-104","soldDate":null,"soldPrice":null}'
        '{"propertyListingState":"SOLD","propertyUnitUid":"31-unit-104","soldDate":"2015-05-11","soldPrice":338800}'
        '{"propertyListingState":"ACTIVE","propertyUnitUid":"31-unit-104","soldDate":null,"soldPrice":null}'
        '{"propertyListingState":"SOLD","propertyUnitUid":"31-unit-104","soldDate":"2025-08-01","soldPrice":2500}'  # a lease
    )
    assert StrataAdapter._past_sales(rsc, "31-unit-104") == [("2015-05-11", 338800.0)]


def test_wahi_sales_leave_out_leases_and_the_current_listing():
    from listingtracker.adapters.wahi import WahiAdapter
    listing = {"mlsId": "E3", "historicalData": [
        {"mlsId": "E1", "statusType": "Sold", "type": "Sale", "soldPrice": "485000", "soldDate": "2024-06-26T04:00:00Z"},
        {"mlsId": "E2", "statusType": "Sold", "type": "Lease", "soldPrice": "2950", "soldDate": "2022-07-18T00:00:00Z"},
        {"mlsId": "E4", "statusType": "Terminated", "type": "Sale", "soldPrice": None, "soldDate": None},
        {"mlsId": "E3", "statusType": "Sold", "type": "Sale", "soldPrice": "500000", "soldDate": "2026-11-01T00:00:00Z"},
    ]}
    assert WahiAdapter._past_sales(listing) == [("2024-06-26", 485000.0)]


def test_seller_paid_the_last_sale_before_this_listing():
    sales = [("2009-03-01", 250000.0), ("2015-05-11", 338800.0), ("2026-11-01", 500000.0)]
    assert ph.last_purchase(sales, "2026-06-23") == ("2015-05-11", 338800.0)
    assert ph.last_purchase(sales, None) == ("2026-11-01", 500000.0)
    assert ph.last_purchase(sales, "2001-01-01") is None
    assert ph.last_purchase([], "2026-06-23") is None


# --- Timeline ---

def test_timeline_keeps_one_entry_per_change():
    site = [("2026-06-23", 475000), ("2026-07-24", 425000)]
    snaps = [snap("2026-07-20", 475000),   # before the site's last entry: the site already covers it
             snap("2026-08-01", 425000),   # same price: not a change
             snap("2026-08-15", 410000)]   # a cut the site's record didn't have yet
    assert ph.timeline(site, snaps) == [
        (d("2026-06-23"), 475000), (d("2026-07-24"), 425000), (d("2026-08-15"), 410000)]


def test_timeline_from_our_own_checks_when_the_site_reports_nothing():
    snaps = [snap("2026-11-09", 339999), snap("2026-11-10", 339999), snap("2026-11-12", 339999)]
    assert ph.timeline([], snaps) == [(d("2026-11-09"), 339999)]


def test_days_on_market_counts_from_the_list_date():
    today = d("2026-11-17")
    assert ph.days_on_market("2026-06-23", snap("2026-11-15", 1), today) == 147
    # once it's off the market, the count stops at the last check
    assert ph.days_on_market("2026-06-23", snap("2026-11-15", 1, Status.SOLD), today) == 145
    assert ph.days_on_market(None, Snapshot(days_on_market=12)) == 12


# --- Forecast ---

NO_MARKET = ph.MarketPattern([], [], [])  # falls back to the defaults
REGULAR = [(d("2026-06-01"), 500000), (d("2026-07-01"), 480000),
           (d("2026-07-31"), 460000), (d("2026-08-30"), 440000)]


def test_forecast_follows_a_regular_rhythm_of_cuts():
    fc = ph.forecast(REGULAR, Status.FOR_SALE, NO_MARKET, today=d("2026-09-05"))
    # three 30-day gaps blended with the 30-day default: next cut 30 days after the last
    assert fc.expected == d("2026-09-29")
    assert fc.confidence == "high"
    assert not fc.overdue
    assert fc.earliest <= fc.expected <= fc.latest
    assert fc.price < 440000
    assert "Cut 3 times" in fc.basis


def test_forecast_is_due_now_once_the_usual_gap_has_passed():
    fc = ph.forecast(REGULAR, Status.FOR_SALE, NO_MARKET, today=d("2026-12-01"))
    assert fc.overdue
    assert fc.expected == fc.earliest == d("2026-12-01")


def test_a_listing_without_cuts_uses_the_markets_time_to_first_cut():
    market = ph.MarketPattern(first_days=[20] * 5, next_days=[], pcts=[0.05] * 5)
    fc = ph.forecast([(d("2026-11-01"), 600000)], Status.FOR_SALE, market, today=d("2026-11-05"))
    assert fc.expected == d("2026-11-21")
    assert fc.confidence == "low"
    assert fc.price == 570000


def test_no_forecast_when_not_for_sale():
    assert ph.forecast(REGULAR, Status.SOLD, NO_MARKET) is None


def test_target_date_steps_through_forecast_cuts():
    fc = ph.forecast(REGULAR, Status.FOR_SALE, NO_MARKET, today=d("2026-09-05"))
    assert ph.target_date(440000, 450000, fc) is None            # already there
    assert ph.target_date(440000, 430000, fc) == fc.expected      # one cut does it
    assert ph.target_date(440000, 400000, fc) > fc.expected
    assert ph.target_date(440000, 1000, fc) is None               # beyond the horizon


def test_trend_is_change_per_month_since_listing():
    points = [(d("2026-01-01"), 500000), (d("2026-03-02"), 450000)]
    assert ph.trend_pct_per_month(points, today=d("2026-03-02")) == pytest.approx(-5.07, abs=0.01)


# --- Storage ---

@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    from listingtracker import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "SEED_URLS", [])
    db.init_db()
    return db


def test_price_history_round_trip_and_cleanup(temp_db):
    db = temp_db
    lid = db.add_listing("https://strata.ca/x/unit-1", "strata.ca").id
    db.save_price_history(lid, "2026-06-23", [("2026-06-23", 475000.0), ("2026-07-24", 425000.0)])
    assert db.get_price_histories(lid) == {lid: ("2026-06-23", [("2026-06-23", 475000.0), ("2026-07-24", 425000.0)])}

    db.save_price_history(lid, None, [])  # a page that reported nothing keeps what we had
    assert db.get_price_histories(lid)[lid][0] == "2026-06-23"

    db.delete_listing(lid)
    assert db.get_price_histories() == {}

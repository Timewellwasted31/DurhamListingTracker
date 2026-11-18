"""Building sales read from strata.ca: which buildings, reading the replies, and what is shown."""

import json
from datetime import date
from types import SimpleNamespace

from listingtracker import buildingsales as bs

B = "whitby/711-rossland-rd-e-the-waldorf-condos"


def listing(id, url, unit="410", fav=False, address="711 Rossland Rd E", city="Whitby"):
    return SimpleNamespace(id=id, url=url, unit=unit, favorited=fav, address=address, city=city)


def test_the_building_is_the_first_two_parts_of_a_strata_address():
    assert bs.building_of(f"https://strata.ca/{B}/unit-505") == B
    assert bs.building_of("https://strata.ca/ajax/10-newing-court/unit-159") == "ajax/10-newing-court"
    assert bs.building_of("https://wahi.com/ca/en/real-estate/x") is None
    assert bs.building_of("https://strata.ca/whitby") is None


def test_due_buildings_put_favourites_first_and_skip_recent_reads_and_houses():
    lst = [
        listing(1, f"https://strata.ca/{B}/unit-505"),
        listing(2, "https://strata.ca/oshawa/1-a-st-condos/unit-1", fav=True),
        listing(3, "https://strata.ca/ajax/5-b-cres/house", unit=None),          # freehold
        listing(4, "https://strata.ca/pickering/9-c-ave-condos/unit-2"),
    ]
    snaps = {i: SimpleNamespace(maintenance_fee=None) for i in (1, 2, 3, 4)}
    checked = {"pickering/9-c-ave-condos": "2026-11-15", B: "2026-11-01"}
    due = bs.buildings_to_read(lst, snaps, checked, today=date(2026, 11, 17))
    assert due == ["oshawa/1-a-st-condos", B]


def reply(*records):
    return {"data": {"listingsNew": {"listings": [{"propertyListing": r} for r in records]}}}


SOLD = {"propertyListingState": "SOLD", "soldPrice": 387500, "soldDate": "2026-09-24", "unit": "501",
        "address": "711 Rossland Rd E", "listPrice": 399000, "bedroomCount": 2, "bathroomCount": 2,
        "sqftSizeHigh": 798, "maintenanceFees": 650.5, "mlsNumber": "E1"}


def test_only_real_sales_are_kept():
    sales = bs.parse_sales(reply(SOLD, {**SOLD, "propertyListingState": "TERMINATED", "soldPrice": None},
                                 {**SOLD, "soldPrice": 2100, "unit": "502"}), B)  # a lease
    assert [(s["unit"], s["sold_price"], s["list_price"], s["sqft"]) for s in sales] == [("501", 387500.0, 399000.0, 798)]


def test_only_the_sold_history_of_this_building_is_read():
    post = lambda states, uid: json.dumps({"operationName": "SEARCH_LISTINGS_QUERY",
                                           "variables": {"filter": {"propertyListingStates": states, "propertyUids": [uid]}}})
    assert bs._is_sales_query(post(["SOLD"], B.split("/")[1]), B)
    assert not bs._is_sales_query(post(["ACTIVE"], B.split("/")[1]), B)
    assert not bs._is_sales_query(post(["SOLD"], "another-building"), B)
    assert not bs._is_sales_query(None, B)


def sale(d, price, beds=2, sqft=800, ask=None):
    return {"unit": "1", "sold_date": d, "sold_price": price, "list_price": ask, "beds": beds,
            "beds_plus": 0, "baths": 2, "sqft": sqft, "parking": 1}


def test_the_summary_compares_the_asking_price_with_the_building():
    sales = [sale("2026-10-01", 400_000, ask=410_000), sale("2026-06-01", 360_000, ask=360_000),
             sale("2026-03-01", 300_000, beds=1, sqft=600), sale("2024-01-01", 500_000)]
    snap = SimpleNamespace(list_price=440_000, beds=2, sqft_max=800, sqft_min=800)
    d = bs.describe(sales, snap, date(2026, 11, 17))
    assert d["count_year"] == 3 and d["basis"] == "last 12 months"
    assert d["median_ppsf"] == 500                        # 400k/800, 360k/800, 300k/600
    assert d["vs_building_pct"] == 10.0                   # 550/sq ft asked
    assert d["alike_count"] == 3 and d["alike_median"] == 400_000
    assert d["vs_alike_pct"] == 10.0
    assert [s["alike"] for s in d["sales"]] == [True, True, False, True]

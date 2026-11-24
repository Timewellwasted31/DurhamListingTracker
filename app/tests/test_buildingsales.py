"""Building sales read from strata.ca: which buildings, reading the replies, and what is shown."""

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


def test_each_building_is_read_once_favourites_first_and_houses_never():
    lst = [
        listing(1, f"https://strata.ca/{B}/unit-505"),
        listing(2, "https://strata.ca/oshawa/1-a-st-condos/unit-1", fav=True),
        listing(3, "https://strata.ca/ajax/5-b-cres/house", unit=None),          # freehold
        listing(4, "https://strata.ca/pickering/9-c-ave-condos/unit-2"),
    ]
    snaps = {i: SimpleNamespace(maintenance_fee=None) for i in (1, 2, 3, 4)}
    checked = {"pickering/9-c-ave-condos": "2026-11-15", B: "2025-01-01"}  # read once: never again
    due = bs.buildings_to_read(lst, snaps, checked)
    assert due == ["oshawa/1-a-st-condos"]
    assert bs.buildings_to_read(lst, snaps, {}) == ["oshawa/1-a-st-condos", B, "pickering/9-c-ave-condos"]


def reply(*records):
    return {"data": {"listingsNew": {"listings": [{"propertyListing": r} for r in records]}}}


SOLD = {"propertyListingState": "SOLD", "soldPrice": 387500, "soldDate": "2026-09-24", "unit": "501",
        "address": "711 Rossland Rd E", "listPrice": 399000, "bedroomCount": 2, "bathroomCount": 2,
        "sqftSizeHigh": 798, "maintenanceFees": 650.5, "mlsNumber": "E1"}


def test_only_real_sales_are_kept():
    sales = bs.parse_sales(reply(SOLD, {**SOLD, "propertyListingState": "TERMINATED", "soldPrice": None},
                                 {**SOLD, "soldPrice": 2100, "unit": "502"}), B)  # a lease
    assert [(s["unit"], s["sold_price"], s["list_price"], s["sqft"]) for s in sales] == [("501", 387500.0, 399000.0, 798)]


class StrataPage:
    """strata.ca as the reader sees it: the building page, then the data service's replies."""

    def __init__(self, sold_pages, status=200, total=None):
        self.sold_pages, self.status, self.total = list(sold_pages), status, total
        self.asked = []

    def get(self, url):
        from listingtracker.reader import Page
        self.asked.append(("page", url))
        return Page(url=url, status=200, html="<html><title>711 Rossland Rd E | Strata</title></html>")

    def call(self, url, body, *, page_origin, headers=None):
        f = body["variables"]["filter"]
        self.asked.append(("data", f["propertyListingStates"], f["ownershipPreference"], f["limit"], f["offset"],
                           headers["Slate-Authorization"], page_origin))
        if f["propertyListingStates"] != ["SOLD"]:
            return 200, reply()
        page = reply(*self.sold_pages.pop(0)) if self.sold_pages else reply()
        page["data"]["listingsNew"]["pagingStats"] = {"totalCount": self.total or 99}
        return self.status, page


HEADERS = {"Slate-Website": "strata", "Slate-PV": "pv", "Slate-Authorization": "token"}


def test_a_building_is_read_with_the_requests_its_page_makes(monkeypatch):
    monkeypatch.setattr(bs, "_pause", lambda *a: None)
    older = {**SOLD, "soldDate": "2020-01-05", "unit": "301"}  # past YEARS_BACK: no more pages
    site = StrataPage([[SOLD, {**SOLD, "unit": "502", "soldDate": "2026-08-01"}], [older]])
    sales = bs.read_building(site, B, HEADERS)
    assert [(s["unit"], s["sold_date"]) for s in sales] == [("501", "2026-09-24"), ("502", "2026-08-01"), ("301", "2020-01-05")]
    assert site.asked == [
        ("page", f"https://strata.ca/{B}"),
        ("data", ["ACTIVE", "SOLD_CONDITIONALLY"], "BUY", 1000, 0, "token", "https://strata.ca"),
        ("data", ["ACTIVE", "SOLD_CONDITIONALLY"], "RENT", 1000, 0, "token", "https://strata.ca"),
        ("data", ["SOLD"], "BUY", 10, 0, "token", "https://strata.ca"),
        ("data", ["SOLD"], "BUY", 8, 10, "token", "https://strata.ca"),
    ]


def test_the_read_stops_at_the_end_of_the_history(monkeypatch):
    monkeypatch.setattr(bs, "_pause", lambda *a: None)
    site = StrataPage([[SOLD]], total=1)
    assert len(bs.read_building(site, B, HEADERS)) == 1 and len(site.asked) == 4


def test_withheld_prices_or_a_refusal_stop_the_run(monkeypatch):
    import pytest
    monkeypatch.setattr(bs, "_pause", lambda *a: None)
    with pytest.raises(bs.StopRun, match="signed out"):  # members-only prices missing: the sign-in ran out
        bs.read_building(StrataPage([[{**SOLD, "soldPrice": None}]]), B, HEADERS)
    with pytest.raises(bs.StopRun, match="signed out"):
        bs.read_building(StrataPage([[SOLD]], status=401), B, HEADERS)
    with pytest.raises(bs.StopRun, match="answered 429"):
        bs.read_building(StrataPage([[SOLD]], status=429), B, HEADERS)


def test_the_request_is_the_pages_own():
    body = bs.listings_request(B, ["SOLD"], 10, 0, "UNAVAILABLE_DATE", "DESC")
    assert list(body) == ["query", "variables", "operationName"]
    assert list(body["variables"]["filter"]) == ["limit", "listingSort", "offset", "ownershipPreference",
                                                 "propertyListingStates", "propertyUids"]
    assert body["variables"]["filter"]["propertyUids"] == ["711-rossland-rd-e-the-waldorf-condos"]
    assert body["query"].startswith("query SEARCH_LISTINGS_QUERY($filter: ListingFilter) {")


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


def test_a_catch_up_read_stops_at_the_sales_already_stored(monkeypatch):
    monkeypatch.setattr(bs, "_pause", lambda *a: None)
    new = {**SOLD, "unit": "900", "soldDate": "2026-11-20"}
    site = StrataPage([[new, SOLD], [{**SOLD, "unit": "100", "soldDate": "2026-01-01"}]])
    sales = bs.read_building(site, B, HEADERS, known={("501", "2026-09-24")})
    assert [s["unit"] for s in sales] == ["900", "501"]
    assert [a[3:5] for a in site.asked if a[0] == "data" and a[1] == ["SOLD"]] == [(10, 0)]  # one page only


def test_comparables_bring_the_newest_sales_of_their_buildings(monkeypatch):
    from listingtracker import db
    fav = SimpleNamespace(id=1, url=f"https://strata.ca/{B}/unit-505", unit="505", city="Whitby", address="711 Rossland Rd E")
    others = {i: SimpleNamespace(id=i, url=f"https://strata.ca/whitby/{i}-a-st-condos/unit-{i}", unit=str(i),
                                 city="Whitby", address=f"{i} A St") for i in range(2, 12)}
    others[3] = SimpleNamespace(id=3, url="https://strata.ca/whitby/9-house-st/house", unit=None, city="Whitby",
                                address="9 House St")  # a house: no building
    monkeypatch.setattr(db, "get_comparables_for_favorite", lambda fid: [{"listing_id": i} for i in others])
    monkeypatch.setattr(db, "get_listing_by_id", lambda i: others.get(i))
    monkeypatch.setattr(db, "get_latest_snapshot", lambda i: SimpleNamespace(maintenance_fee=None))
    monkeypatch.setattr(db, "get_building_sales", lambda b=None: [])
    state = {bs.STATE_LATEST + "whitby/2-a-st-condos": date.today().isoformat()}  # looked at today
    monkeypatch.setattr(db, "get_state", lambda k: state.get(k))
    monkeypatch.setattr(bs, "needs_sign_in", lambda: False)
    monkeypatch.setattr(bs, "paused_until", lambda: None)
    read = []
    monkeypatch.setattr(bs, "_read_buildings", lambda b, cities, cancel=None, newest_only=False:
                        read.append((b, newest_only)) or {"buildings": len(b), "sales": 0, "stopped": None})

    assert bs.buildings_for_comparables(fav)[:3] == [B, "whitby/2-a-st-condos", "whitby/4-a-st-condos"]
    bs.catch_up([fav])
    assert read == [([B, "whitby/4-a-st-condos", "whitby/5-a-st-condos", "whitby/6-a-st-condos",
                      "whitby/7-a-st-condos", "whitby/8-a-st-condos"], True)]  # at most 6, own building first


def test_a_building_never_read_in_full_gets_its_full_read(monkeypatch):
    from listingtracker import db, reader, siteaccounts
    monkeypatch.setattr(bs, "_pause", lambda *a: None)
    state, saved = {bs.STATE_CHECKED + "whitby/old-condos": "2025-01-01"}, []
    monkeypatch.setattr(db, "get_state", lambda k: state.get(k))
    monkeypatch.setattr(db, "set_state", lambda k, v: state.__setitem__(k, v))
    monkeypatch.setattr(db, "get_building_sales", lambda b=None: [{"unit": "501", "sold_date": "2026-09-24"}])
    monkeypatch.setattr(db, "save_building_sales", saved.extend)
    monkeypatch.setattr(db, "save_area_sales", lambda s: None)
    calls = []
    monkeypatch.setattr(bs, "read_building", lambda r, b, h, cancel=None, known=None:
                        calls.append((b, known)) or [{**bs.parse_sales(reply(SOLD), b)[0]}])

    class Session:
        def __init__(self, urls): pass
        def stored(self, origin, name): return "value"
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(bs, "Reader", Session)
    bs._read_buildings(["whitby/old-condos", "whitby/new-condos"], {}, newest_only=True)
    assert calls == [("whitby/old-condos", {("501", "2026-09-24")}), ("whitby/new-condos", None)]
    assert len(saved) == 1  # the old building's 501 was already stored; the new building's is new
    assert state[bs.STATE_CHECKED + "whitby/new-condos"] == date.today().isoformat()


def test_rescanning_one_listing_on_the_phone_catches_up_its_buildings(monkeypatch):
    from listingtracker import api, comparables, db, buildingsales
    lst = SimpleNamespace(id=5, favorited=True)
    monkeypatch.setattr(db, "get_listing_by_id", lambda i: lst if i == 5 else None)
    monkeypatch.setattr(comparables, "scan_comparables", lambda favs: (3, []))
    started = []
    monkeypatch.setattr(buildingsales, "catch_up_soon", started.append)
    assert api.scan_comparables(listing_id=5)["count"] == 3 and started == [[5]]

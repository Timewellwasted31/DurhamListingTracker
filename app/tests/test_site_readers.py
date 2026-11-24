"""The Zolo, Realtor.ca and REW readers, on pages laid out the way those sites lay them out."""

from datetime import date, timedelta

import pytest

from listingtracker import fetcher, search
from listingtracker.adapters.base import money, property_kind, split_unit, sqft_range
from listingtracker.adapters.realtor import RealtorAdapter
from listingtracker.adapters.rew import RewAdapter
from listingtracker.adapters.zolo import ZoloAdapter
from listingtracker.models import Listing

ADDED = (date.today() - timedelta(days=3)).strftime("%b %d, %Y").replace(" 0", " ")


def zolo_page(title="98 - 1780 Simcoe Street N, Oshawa &#8212; For Sale @ $428,999 &#124; Zolo.ca"):
    return f"""<html><head><title>{title}</title>
<meta name="description" content="FOR SALE &ndash; 98 - 1780 Simcoe Street N, Oshawa ON &bull; 3 bed &bull; 3 bath &bull; 1000-1199 sqft Condo &bull; MLS# E14200430" />
<script type="application/ld+json">{{"@type": "Event", "location": {{"address": {{"streetAddress": "89 Harmony Road N", "addressLocality": "Oshawa"}}}}}}</script>
</head><body>
<h1 class="address xs-text-4 sm-text-3 truncate heavy"> 98 - 1780 Simcoe Street N </h1>
<div class="area truncate xs-text-5"><a href="/oshawa-real-estate" class="text-primary">Oshawa</a>, <a href="/oshawa-real-estate/samac">Samac</a></div>
<dl class="column"><dt class="column-label">Type</dt><dd class="column-value"><span class="priv">Common Element Condo</span></dd></dl>
<dl class="column"><dt class="column-label">Size</dt><dd class="column-value"><span class="priv">1000-1199 sqft</span></dd></dl>
<dl class="column"><dt class="column-label">Taxes</dt><dd class="column-value"><span class="priv">$5,133 /yr</span></dd></dl>
<dl class="column"><dt class="column-label">Maintenance Fees</dt><dd class="column-value"><span class="priv">$610 /mo</span></dd></dl>
<dl class="column"><dt class="column-label">Added</dt><dd class="column-value"><span class="priv">{ADDED} (3 days ago)</span></dd></dl>
<dl class="column"><dt class="column-label">MLS&reg;#</dt><dd class="column-value"><span class="priv">E14200430</span></dd></dl>
<dl class="column"><dt class="column-label">Listed By</dt><dd class="column-value">CENTURY 21 TITANS REALTY INC.</dd></dl>
<div class="column"><div class="column-label">Unit Exposure</div><div class="column-value"><span class="priv">East</span></div></div>
<div class="column"><div class="column-label">Patio Terrace</div><div class="column-value"><span class="priv">Open</span></div></div>
<div class="column"><div class="column-label">Ensuite Laundry</div><div class="column-value"><span class="priv">Y</span></div></div>
<div class="column"><div class="column-label">Parking Total</div><div class="column-value"><span class="priv">1</span></div></div>
<div class="column"><div class="column-label">Lockers</div><div class="column-value"><span class="priv">Owned</span></div></div>
<section id="sold-history"><table>
<tr class="table-entry"><td class="table-mls sm-col-3">E14200430</td><td class="table-date nowrap">Nov 1, 2026</td><td class="table-event">Listed</td><td class="table-price">$449,000</td></tr>
<tr class="table-entry"><td class="table-mls sm-col-3"></td><td class="table-date nowrap">Nov 10, 2026</td><td class="table-event">Price Change</td><td class="table-price">$428,999</td></tr>
<tr class="table-entry"><td class="table-mls sm-col-3">E9000001</td><td class="table-date nowrap">Mar 3, 2021</td><td class="table-event">Sold</td><td class="table-price">$361,000</td></tr>
<tr class="table-entry"><td class="table-mls sm-col-3">XXXXXXXXXX</td><td class="table-date nowrap">Xxx XX, XXXX</td><td class="table-event">Xxxx</td><td class="table-price">$X,XXX,XXX</td></tr>
</table></section></body></html>"""


def test_zolo_reads_this_listing_and_not_the_open_houses_nearby():
    r = ZoloAdapter().parse(zolo_page())
    assert (r["unit"], r["address"], r["city"], r["neighbourhood"]) == ("98", "1780 Simcoe Street N", "Oshawa", "Samac")
    assert (r["status"], r["list_price"], r["mls_number"]) == ("FOR_SALE", 428999.0, "E14200430")
    assert (r["beds"], r["baths"], r["sqft_min"], r["sqft_max"]) == (3, 3, 1000, 1199)
    assert (r["taxes_annual"], r["maintenance_fee"]) == (5133.0, 610.0)  # not the price in the taxes
    assert r["property_type"] == "Common Element Condo" and r["broker"] == "CENTURY 21 TITANS REALTY INC."
    assert (r["exposure"], r["outdoor_space"], r["ensuite_laundry"], r["parking"], r["locker"]) == (
        "East", "Balcony", "Yes", "1 space", "Owned")
    assert r["days_on_market"] == 3 and not r.get("is_rental") and r["parse_errors"] is None
    assert r["price_history"] == [("2026-11-01", 449000.0), ("2026-11-10", 428999.0)]
    assert r["past_sales"] == [("2021-03-03", 361000.0)]  # the masked row (signed out) is skipped


def test_zolo_rentals_and_category_pages():
    r = ZoloAdapter().parse(zolo_page(title="1 A St, Oshawa — For Rent @ $2,300 | Zolo.ca"))
    assert r["is_rental"]
    ok = ZoloAdapter.is_listing_url
    assert ok("https://www.zolo.ca/oshawa-real-estate/1018-pinetree-court")
    assert ok("https://www.zolo.ca/oshawa-real-estate/1780-simcoe-street-north/98")
    assert not ok("https://www.zolo.ca/oshawa-real-estate/1-bedroom-apartments-for-rent")
    assert not ok("https://www.zolo.ca/oshawa-real-estate/eastdale")
    assert not ok("https://www.zolo.ca/oshawa-real-estate/condos/page-2")


REALTOR_PAGE = """<html><head><title>For sale: 429 - 50 RICHMOND STREET E, Oshawa (O'Neill), Ontario L1G7C7 - E14205508 | REALTOR.ca</title></head><body>
<h1 id="listingAddress" class="unsetH1">429 - 50 RICHMOND STREET E<br>Oshawa (O'Neill), Ontario L1G7C7</h1>
<div id="listingMLSNum">MLS<sup>&reg;</sup> Number: <span id="MLNumberVal">E14205508</span></div>
<div id="listingPriceValue">$392,500 </div>
<div class="listingIconCon" id="BedroomIcon"><div class="listingIconNum" data-binding="innertext=NumberVal">3 + 1</div></div>
<div class="listingIconCon" id="BathroomIcon"><div class="listingIconNum" data-binding="innertext=NumberVal">2</div></div>
<div class="propertyDetailsSectionContentLabel">Building Type</div> <div class="propertyDetailsSectionContentValue"> Apartment </div>
<div class="propertyDetailsSectionContentLabel">Square Footage</div> <div class="propertyDetailsSectionContentValue"> 1000 - 1199 sqft </div>
<div class="propertyDetailsSectionContentLabel">Title</div> <div class="propertyDetailsSectionContentValue"> Condominium/Strata </div>
<div class="propertyDetailsSectionContentLabel">Annual Property Taxes</div> <div class="propertyDetailsSectionContentValue"> $2,945.89<span class='listingCADLabel' style='display:none'> (CAD)</span> </div>
<div class="propertyDetailsSectionContentLabel">Time on REALTOR.ca</div> <div class="propertyDetailsSectionContentValue"> 5 days </div>
<div class="propertyDetailsSectionContentLabel">Features</div> <div class="propertyDetailsSectionContentValue"> Elevator, Balcony, In suite Laundry </div>
<div class="propertyDetailsSectionContentLabel">Maintenance Fees</div> <div class="propertyDetailsSectionContentValue"> $863.99 Monthly </div>
<div class="propertyDetailsSectionContentLabel">Total Parking Spaces</div> <div class="propertyDetailsSectionContentValue"> 1 </div>
<div class="officeCardName" data-binding="innertext=Name">CENTURY 21 LEADING EDGE REALTY INC.</div>
</body></html>"""


def test_realtor_reads_the_heading_icons_and_labelled_details():
    r = RealtorAdapter().parse(REALTOR_PAGE)
    assert (r["unit"], r["address"], r["city"], r["neighbourhood"]) == ("429", "50 Richmond Street E", "Oshawa", "O'Neill")
    assert (r["status"], r["list_price"], r["mls_number"]) == ("FOR_SALE", 392500.0, "E14205508")
    assert (r["beds"], r["baths"], r["sqft_min"], r["sqft_max"]) == (3, 2, 1000, 1199)
    assert (r["taxes_annual"], r["maintenance_fee"], r["parking"]) == (2945.89, 863.99, "1 space")
    assert r["property_type"] == "Condo Apartment" and r["days_on_market"] == 5
    assert (r["ensuite_laundry"], r["outdoor_space"]) == ("Yes", "Balcony")
    assert r["broker"] == "CENTURY 21 LEADING EDGE REALTY INC." and r["parse_errors"] is None
    assert RealtorAdapter.is_listing_url("https://www.realtor.ca/real-estate/30540033/429-50-richmond-street-e-oshawa-oneill")
    assert not RealtorAdapter.is_listing_url("https://www.realtor.ca/on/oshawa/condos-for-sale")


REW_PAGE = """<html><head><title>For Sale: 1003-900 Wilson Road N, Oshawa, ON - REW</title>
<script type='application/ld+json'>{"address":{"streetAddress":"1003-900 Wilson Road N","addressLocality":"Centennial"}}</script></head><body>
<span data-listing-address='1003-900 Wilson Road N, Oshawa ON, L1G 7T2' data-listing-city='Oshawa' data-listing-neighborhood='Centennial'></span>
<ul><li data-listing-num-bedrooms='2'>2 Bed</li><li data-listing-num-bathrooms='2'>2 Bath</li><li data-listing-sqft='1400 - 1598'>1400 - 1598 Sqft</li></ul>
<div class='lineddisplay'><section><div>List Price</div><div class='flex'><div>$719,000</div><div><a>Get Pre-Approved</a></div></div></section>
<section><div>Gross Taxes for 2026</div><div>$5,718</div></section><section><div>Strata Maintenance Fees</div><div>$1,233 Monthly</div></section></div>
<section><div>Parking Spaces</div><div>1</div></section><section><div>Property Type</div><div>Apt/Condo</div></section>
<section><div>Title</div><div>Condo/Strata</div></section><section><div>Primary Broker</div><div>DAN PLOWMAN TEAM REALTY INC.</div></section>
<section><div>Days on REW</div><div>20 Days</div></section><section><div>MLS&reg; Number</div><div>E14154654</div></section>
<p>Sold out building, sold quickly last time</p>
</body></html>"""


def test_rew_takes_the_town_from_the_header_not_the_neighbourhood():
    r = RewAdapter().parse(REW_PAGE)
    assert (r["unit"], r["address"], r["city"], r["neighbourhood"]) == ("1003", "900 Wilson Road N", "Oshawa", "Centennial")
    assert (r["status"], r["list_price"], r["mls_number"]) == ("FOR_SALE", 719000.0, "E14154654")  # "sold" in the text is not Sold
    assert (r["beds"], r["baths"], r["sqft_min"], r["sqft_max"]) == (2, 2, 1400, 1598)
    assert (r["taxes_annual"], r["maintenance_fee"], r["parking"]) == (5718.0, 1233.0, "1 space")
    assert (r["property_type"], r["broker"], r["days_on_market"]) == ("Condo Apartment", "DAN PLOWMAN TEAM REALTY INC.", 20)
    assert RewAdapter.is_listing_url("https://www.rew.ca/properties/th57-1865-kingston-road-pickering-on")
    assert not RewAdapter.is_listing_url("https://www.rew.ca/properties/areas/oshawa-on/page/2")


def test_shared_helpers():
    assert split_unit("98 - 1780 Simcoe Street N") == ("98", "1780 Simcoe Street N")
    assert split_unit("TH57-1865 Kingston Road") == ("TH57", "1865 Kingston Road")
    assert split_unit("1018 Pinetree Court") == (None, "1018 Pinetree Court")
    assert sqft_range("1400 - 1598 Sqft") == (1400, 1598) and sqft_range("No Data") == (None, None)
    assert money("$1,233 Monthly") == 1233.0 and money("none") is None
    assert property_kind("Apt/Condo", "Condo/Strata") == "Condo Apartment"
    assert property_kind("Row / Townhouse", "Condominium/Strata") == "Condo Townhouse"
    assert property_kind("Townhouse", "Freehold") == "Townhouse"
    assert property_kind("Att/Row/Twnhouse") == "Att/Row/Twnhouse"
    assert property_kind("House", "Freehold") == "House"  # not assumed detached


def test_results_pages_stay_in_town_and_page_on():
    assert search.in_searched_town("https://www.zolo.ca/oshawa-real-estate/1-a-st", "zolo.ca", "Oshawa")
    assert not search.in_searched_town("https://www.zolo.ca/calgary-real-estate/1719-11-avenue/203", "zolo.ca", "Oshawa")
    assert search.in_searched_town("https://www.rew.ca/properties/1-a-st-oshawa-on", "rew.ca", "Oshawa")
    assert search.next_page("https://www.rew.ca/properties/areas/oshawa-on/type/townhouse?list_price_to=5",
                            "rew.ca", 2) == "https://www.rew.ca/properties/areas/oshawa-on/type/townhouse/page/2?list_price_to=5"
    assert search.next_page("https://www.zolo.ca/oshawa-real-estate/condos", "zolo.ca", 3) == \
        "https://www.zolo.ca/oshawa-real-estate/condos/page-3"
    assert search.next_page("https://strata.ca/oshawa/condos-for-sale", "strata.ca", 2) is None


def zolo_card(path, price, mls):
    return (f'<article class="card-listing"><span itemprop="price" value="{price}">{price:,}</span>'
            f'<span>MLS&reg; {mls}</span><a href="https://www.zolo.ca{path}">x</a></article>')


class FakeReader:
    signed_in = False

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url):
        from listingtracker.reader import Page
        self.asked.append(url)
        return Page(url=url, status=200, html=self.pages.get(url, "<html></html>"))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_search_passes_over_listings_elsewhere_over_budget_or_already_tracked(monkeypatch):
    base = "https://www.zolo.ca/oshawa-real-estate/condos"
    pages = {
        base: zolo_card("/oshawa-real-estate/1-a-st/1", 400_000, "E10000001")
              + zolo_card("/oshawa-real-estate/2-b-st/2", 900_000, "E10000002")   # over budget
              + zolo_card("/oshawa-real-estate/3-c-st/3", 410_000, "E10000003")   # tracked from Strata
              + zolo_card("/calgary-real-estate/4-d-st/4", 300_000, "E10000004"),  # another town
        base + "/page-2": zolo_card("/oshawa-real-estate/5-e-st/5", 420_000, "E10000005"),
        base + "/page-3": zolo_card("/oshawa-real-estate/5-e-st/5", 420_000, "E10000005"),  # past the end
    }
    reader = FakeReader(pages)
    monkeypatch.setattr(fetcher, "Reader", lambda urls: reader)
    monkeypatch.setattr(fetcher, "_check_robots", lambda *a: True)
    monkeypatch.setattr(fetcher.time, "sleep", lambda s: None)
    found = fetcher._search_one_site(
        [{"site": "zolo.ca", "city": "Oshawa", "url": base, "description": "zolo", "max_price": 495_000}],
        None, None, None, [0], 1, frozenset({"E10000003"}))
    assert found == {"https://www.zolo.ca/oshawa-real-estate/1-a-st/1", "https://www.zolo.ca/oshawa-real-estate/5-e-st/5"}
    assert reader.asked == [base, base + "/page-2", base + "/page-3"]


def test_a_strata_copy_replaces_one_just_found_on_another_site(monkeypatch):
    from datetime import datetime
    deleted = []
    now = datetime.utcnow().isoformat()
    monkeypatch.setattr(fetcher, "_check_robots", lambda *a: True)
    monkeypatch.setattr(fetcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetcher, "get_latest_snapshot", lambda lid: None)
    monkeypatch.setattr(fetcher, "listings_with_mls", lambda mls, other_than: [
        {"id": 9, "site": "condos.ca", "favorited": 0, "user_notes": "", "date_added": now},
        {"id": 3, "site": "rew.ca", "favorited": 1, "user_notes": "", "date_added": now}])  # a favourite stays
    monkeypatch.setattr(fetcher, "delete_listing", deleted.append)
    for name in ("update_listing", "save_price_history", "save_past_sales", "save_area_sales", "save_listing_images"):
        monkeypatch.setattr(fetcher, name, lambda *a, **k: None)
    monkeypatch.setattr(fetcher, "add_snapshot", lambda snap: 1)
    monkeypatch.setattr(fetcher, "_store_html", lambda html, sid: "x")
    monkeypatch.setattr(fetcher, "get_listing_images", lambda lid: [1])
    monkeypatch.setattr(fetcher, "diff_snapshots", lambda *a: [])

    class Strata:
        site_name = "strata.ca"
        def matches(self, url): return True
        def detect_blocked(self, html): return False
        def parse(self, html, url=""): return {"mls_number": "E1", "status": "FOR_SALE", "list_price": 400000.0,
                                               "address": "1 A St"}
    listing = Listing(id=40, url="https://strata.ca/oshawa/a-condos/unit-1", site="strata.ca")
    snap, _ = fetcher.fetch_listing(listing, FakeReader({listing.url: "x" * 3000}), Strata())
    assert snap is not None and deleted == [9]


def test_a_home_already_tracked_from_another_site_is_not_added_twice(monkeypatch):
    deleted, saved = [], []
    monkeypatch.setattr(fetcher, "_check_robots", lambda *a: True)
    monkeypatch.setattr(fetcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetcher, "get_latest_snapshot", lambda lid: None)  # just found by a search
    monkeypatch.setattr(fetcher, "listings_with_mls", lambda mls, other_than: [
        {"id": 7, "site": "strata.ca", "favorited": 0, "user_notes": "", "date_added": "2026-01-01"}]
        if mls == "E14205508" else [])
    monkeypatch.setattr(fetcher, "delete_listing", deleted.append)
    monkeypatch.setattr(fetcher, "update_listing", saved.append)
    listing = Listing(id=40, url="https://www.realtor.ca/real-estate/30540033/429-50-richmond-street-e-oshawa-oneill",
                      site="realtor.ca")
    page = FakeReader({listing.url: REALTOR_PAGE + "<!--" + "-" * 3000 + "-->"})  # a real page's size
    assert fetcher.fetch_listing(listing, page, RealtorAdapter()) == (None, [])
    assert deleted == [40] and saved == []


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    from listingtracker import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "SEED_URLS", [])
    db.init_db()
    return db


def test_a_home_found_on_two_sites_in_one_search_is_kept_once(temp_db):
    from listingtracker.models import Snapshot, Status
    db = temp_db

    def tracked(url, site, mls, favourite=False, days_ago=0):
        lst = db.add_listing(url, site)
        lst.mls_number, lst.address = mls, "1 A St"
        db.update_listing(lst)
        db.add_snapshot(Snapshot(listing_id=lst.id, fetched_at="2026-11-24T00:00:00", status=Status.FOR_SALE,
                                 list_price=400000.0))
        if favourite:
            db.toggle_favorite(lst.id)
        if days_ago:
            with db._connect() as conn:
                conn.execute("UPDATE listings SET date_added = datetime('now', ?) WHERE id = ?",
                             (f"-{days_ago} days", lst.id))
                conn.commit()
        return lst.id

    rew = tracked("https://www.rew.ca/properties/407-2550-simcoe-street-n-oshawa-on", "rew.ca", "E13747880")
    zolo = tracked("https://www.zolo.ca/oshawa-real-estate/2550-simcoe-street-north/407", "zolo.ca", "E13747880")
    condos_new = tracked("https://condos.ca/oshawa/z/unit-9-E3", "condos.ca", "E3")
    strata_new = tracked("https://strata.ca/oshawa/z/unit-9", "strata.ca", "E3")
    noted = tracked("https://www.rew.ca/properties/9-z-st-oshawa-on", "rew.ca", "E3")
    old_strata = tracked("https://strata.ca/oshawa/x/unit-5", "strata.ca", "E1", days_ago=30)
    old_condos = tracked("https://condos.ca/oshawa/x/unit-5-E1", "condos.ca", "E1", days_ago=20)
    first = tracked("https://strata.ca/oshawa/y/unit-6", "strata.ca", "E2")
    liked = tracked("https://www.zolo.ca/oshawa-real-estate/y/6", "zolo.ca", "E2", favourite=True)
    with db._connect() as conn:
        conn.execute("UPDATE listings SET user_notes = 'call agent' WHERE id = ?", (noted,))
        conn.commit()
    db.cleanup_bad_listings(after_fetch=True)
    kept = {lst.id for lst in db.get_all_listings(active_only=False)}
    assert zolo in kept and rew not in kept  # the better site's copy, whichever was found first
    assert strata_new in kept and condos_new not in kept  # Strata carries the building's sales
    assert noted in kept  # one with notes is never removed
    assert {old_strata, old_condos} <= kept  # duplicates from before are left alone
    assert {first, liked} <= kept  # a favourite is never removed


def test_condos_rentals_are_recognised():
    from listingtracker.adapters.condos import CondosAdapter
    rent = "<html><head><title>Room A - 33 Lurcook St, Ajax | 3Beds/2Bath, Townhouse, $1,100/mo | Condos.ca</title></head></html>"
    sale = "<html><head><title>9 - 2800 Courtice Road, Courtice | 3+1Beds/3Bath, Townhouse, $589,900 | Condos.ca</title></head></html>"
    assert CondosAdapter().parse(rent, url="https://condos.ca/ajax/x/unit-RoomA-E13830402").get("is_rental")
    assert not CondosAdapter().parse(sale, url="https://condos.ca/clarington/x/unit-9-E14197388").get("is_rental")



def test_strata_takes_the_unit_from_the_address_when_the_page_leaves_it_out():
    from listingtracker.adapters.strata import StrataAdapter
    r = StrataAdapter().parse("<html><body>" + "x" * 3000 + "</body></html>",
                              url="https://strata.ca/ajax/1-120-wilkie-ln-carruthers-forest/105-unit-105")
    assert r["unit"] == "105"

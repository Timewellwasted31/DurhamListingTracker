"""Estimating what a seller paid when no sale is on record."""

import pytest

from listingtracker import purchaseprice as pp
from listingtracker.purchaseprice import Home, Sale


def test_site_types_map_to_index_types():
    assert pp.type_group("Condo Apt") == "apartment"
    assert pp.type_group("Condo Apartment") == "apartment"
    assert pp.type_group("Att/Row/Twnhouse") == "townhouse"
    assert pp.type_group("Condo Townhouse") == "townhouse"
    assert pp.type_group("Semi-Detached") == "semi"   # not "detached"
    assert pp.type_group("Detached") == "detached"
    assert pp.type_group(None) is None


def test_sizes_and_buildings():
    assert pp.parse_sqft("1000-1199") == 1100
    assert pp.parse_sqft("1,350") == 1350
    assert pp.parse_sqft(None) is None
    assert pp.building_key("711 Rossland Rd E") == pp.building_key("711 Rossland Rd") == "711 rossland"
    assert pp.building_key("Rossland Rd") is None


def test_older_sales_are_brought_to_todays_money():
    sale = Sale("2016-06-01", 285_000, "Oshawa", None, "townhouse", 3, 1200)
    assert sale.today_price == pytest.approx(285_000 / pp.PRICE_LEVEL[2016][pp.TYPES.index("townhouse")])


def towns(n, price=600_000, city="Oshawa", sqft=1200, beds=3, year="2026"):
    return [Sale(f"{year}-0{1 + i % 9}-01", price, city, f"{100 + i} Main St", "townhouse", beds, sqft) for i in range(n)]


def test_values_from_similar_homes_in_the_city_by_size():
    home = Home("Oshawa", "5 Elm St", "townhouse", 3, 1500, None)
    value, n, scope = pp.value_today(home, towns(6))
    assert value == pytest.approx(600_000 / 1200 * 1500) and n == 6 and scope == "similar homes in Oshawa"


def test_the_buildings_own_sales_come_first():
    home = Home("Oshawa", "711 Rossland Rd E", "apartment", 2, 900, None)
    building = [Sale("2026-03-01", 450_000, "Oshawa", "711 Rossland Rd", "apartment", 2, 900),
                Sale("2026-05-01", 470_000, "Oshawa", "711 Rossland Rd E", None, 2, 880)]
    elsewhere = [Sale("2026-04-01", 900_000, "Oshawa", f"{i} Other St", "apartment", 2, 900) for i in range(9)]
    value, n, scope = pp.value_today(home, building + elsewhere)
    assert scope == "this building" and n == 2 and value < 500_000


def test_falls_back_to_durham_when_the_city_has_too_few():
    home = Home("Brock", "1 Lake St", "townhouse", 3, 1200, None)
    assert pp.value_today(home, towns(6))[2] == "similar homes in Durham"


def test_leases_and_the_homes_own_sale_are_ignored():
    home = Home("Oshawa", "5 Elm St", "townhouse", 3, 1200, None)
    pool = towns(5) + [Sale("2026-02-01", 2_500, "Oshawa", "9 Elm St", "townhouse", 3, 1200)]
    assert pp.value_today(home, pool)[1] == 5
    own = frozenset({(pool[0].sold_date, pool[0].price)})
    assert pp.value_today(home, pool, exclude=own) is None  # 4 left, fewer than needed


def test_no_type_means_only_the_building_counts():
    home = Home("Oshawa", "5 Elm St", None, 3, 1200, None)
    assert pp.value_today(home, towns(9)) is None


KNOWN_HOMES = (
    [("5", 450, 1, 1, 650, "Condo Apt")] * 4 + [("8", 520, 2, 2, 900, "Condo Apt")] * 4
    + [("12", 300, 3, 3, 1800, "Condo Townhouse")] * 6
    + [(None, None, 4, 3, 2400, "Detached")] * 6
)


def test_type_guesser_uses_the_most_alike_listings():
    g = pp.TypeGuesser(KNOWN_HOMES)
    assert g.guess("3", 400, 1, 1, 700) == "apartment"
    assert g.guess("61", None, 3, 3, 2000) == "townhouse"   # a unit number, fee unknown
    assert g.guess(None, None, 4, 3, 2500) == "detached"
    assert g.guess(None, None, None, 2, 1500) is None          # too little to go on


class Snap:
    def __init__(self, **kw):
        self.property_type = kw.get("property_type"); self.maintenance_fee = kw.get("fee")
        self.beds, self.baths = kw.get("beds"), kw.get("baths")
        self.sqft_max = self.sqft_min = kw.get("sqft"); self.year_built = None


class Lst:
    def __init__(self, city, unit=None, address="1 Main St"):
        self.city, self.unit, self.address = city, unit, address


def test_home_infers_a_missing_type_and_says_so():
    g = pp.TypeGuesser(KNOWN_HOMES)
    home = pp.home_for(Lst("Pickering", "61"), Snap(beds=3, baths=3, sqft=2000), g)
    assert (home.group, home.group_inferred) == ("townhouse", True)
    est = pp.estimate(home, towns(6, city="Pickering"), this_year=2026)
    assert est.type_basis == "Type inferred as townhouse from size, bedrooms and bathrooms"
    # a listed type is used as is
    assert pp.home_for(Lst("Pickering"), Snap(property_type="Detached", beds=3), g).group_inferred is False
    # an entry with no city (a site's category page, not a home) gets no inferred type
    assert pp.home_for(Lst(""), Snap(beds=3, baths=3, sqft=2000), g).group is None


def test_purchase_year_is_the_build_year_for_newer_homes():
    assert pp.assumed_purchase_year(2018, 2026) == (2018, "Built 2018, so likely bought new")
    assert pp.assumed_purchase_year(1989, 2026)[0] == 2016
    assert pp.assumed_purchase_year(None, 2026)[0] == 2016


def test_estimate_scales_todays_value_to_the_purchase_year():
    home = Home("Oshawa", "5 Elm St", "townhouse", 3, 1200, 2018)
    est = pp.estimate(home, towns(6), this_year=2026)
    assert est.year == 2018
    assert est.price == round(600_000 * pp.PRICE_LEVEL[2018][pp.TYPES.index("townhouse")], -3)

"""Comparables for a favorite: cheaper homes at least as big, and similar pricier ones."""

from types import SimpleNamespace

from listingtracker.comparables import at_least_as_big, find_comparables_for_favorite
from listingtracker.models import Status


def snap(price, beds, baths, sqft, kind="Condo Apartment"):
    return SimpleNamespace(list_price=price, beds=beds, baths=baths, sqft_max=sqft, sqft_min=sqft,
                           property_type=kind, status=Status.FOR_SALE)


def test_at_least_as_big_allows_a_small_margin_on_floor_area():
    assert at_least_as_big(2, 2, 1000, 3, 2, 1144)
    assert at_least_as_big(2, 2, 1000, 2, 2, 960)        # within 5%
    assert not at_least_as_big(2, 2, 1000, 2, 2, 900)
    assert not at_least_as_big(2, 2, 1000, 2, 1, 1100)   # a bathroom fewer
    assert not at_least_as_big(2, 2, 1000, 1, 2, 1100)   # a bedroom fewer
    assert not at_least_as_big(2, 2, 1000, 2, 2, None)   # size unknown: can't tell it's as big
    assert at_least_as_big(2, 2, None, 2, 2, None)       # the favorite's size unknown


def test_cheaper_homes_at_least_as_big_are_listed_first():
    fav = SimpleNamespace(id=1, city="Oshawa")
    listings = [SimpleNamespace(id=i, city="Oshawa") for i in range(2, 9)] + [SimpleNamespace(id=9, city="Ajax")]
    snaps = {
        1: snap(334_900, 2, 2, 1000),
        2: snap(299_000, 3, 2, 1144),     # cheaper and bigger: listed despite the lower price
        3: snap(339_999, 3, 3, 1070),     # a bedroom more at a similar price: no longer left out
        4: snap(349_000, 2, 2, 979),      # similar, pricier
        5: snap(280_000, 2, 1, 900),      # cheaper but smaller
        6: snap(2_100, 2, 2, 1000),       # a lease
        7: snap(360_000, 1, 1, 600),      # a bedroom fewer
        8: snap(700_000, 2, 2, 1000),     # more than twice the price
        9: snap(250_000, 3, 2, 1200),     # another city
    }
    found = [i for i, _ in find_comparables_for_favorite(fav, snaps[1], listings, snaps)]
    assert found[0] == 2
    assert set(found) == {2, 3, 4}

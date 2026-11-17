"""What a seller likely paid, when the listing sites have no sale on record for the home.

Two steps:
  1. Value the home in today's money from sales of similar homes: the same building first, then
     the same city, type, bedrooms and size. Each sale is first brought to today's money with
     Durham's price index, so older sales count too.
  2. Take that value back to the year the seller most likely bought: the year the home was built
     if it is recent (a newer home with no resale on record was most likely bought from the
     builder), otherwise about ten years ago, roughly a typical stay.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

# Durham Region price level by year and home type, relative to July 2026 (= 1.000).
# From TRREB's MLS Home Price Index public tables for Durham Region, July of each year:
# 2021-2026 chained back from the July 2026 table with each year's reported year-over-year change
# (TRREB revises past index levels each year, so levels from different tables don't mix);
# 2019-2021 bridged with the 2019 and 2022 tables' levels, 2020 placed at Durham's reported +15.06%
# year-over-year (August 2020, Durham Region Association of REALTORS); 2014-2019 chained with each
# table's yearly change; 2005 is the index base (January 2005 = 100), with 2006-2013 filled evenly.
#           detached   semi  townhouse apartment composite
PRICE_LEVEL = {
    2005: (0.283, 0.264, 0.284, 0.374, 0.279),
    2006: (0.296, 0.277, 0.295, 0.390, 0.292),
    2007: (0.309, 0.290, 0.307, 0.406, 0.306),
    2008: (0.323, 0.305, 0.319, 0.423, 0.320),
    2009: (0.338, 0.320, 0.331, 0.441, 0.334),
    2010: (0.353, 0.336, 0.344, 0.460, 0.350),
    2011: (0.369, 0.353, 0.358, 0.480, 0.366),
    2012: (0.385, 0.370, 0.372, 0.500, 0.382),
    2013: (0.403, 0.389, 0.386, 0.521, 0.400),
    2014: (0.421, 0.408, 0.401, 0.543, 0.418),
    2015: (0.477, 0.468, 0.445, 0.587, 0.473),
    2016: (0.574, 0.570, 0.530, 0.647, 0.570),
    2017: (0.693, 0.672, 0.665, 0.804, 0.689),
    2018: (0.661, 0.649, 0.631, 0.808, 0.660),
    2019: (0.676, 0.668, 0.661, 0.829, 0.677),
    2020: (0.778, 0.771, 0.773, 0.897, 0.779),
    2021: (1.024, 1.020, 1.050, 1.047, 1.025),
    2022: (1.145, 1.149, 1.241, 1.297, 1.153),
    2023: (1.163, 1.173, 1.196, 1.231, 1.166),
    2024: (1.102, 1.111, 1.145, 1.154, 1.104),
    2025: (1.053, 1.055, 1.075, 1.078, 1.054),
    2026: (1.000, 1.000, 1.000, 1.000, 1.000),
}
TYPES = ("detached", "semi", "townhouse", "apartment", "composite")

RECORDS_FROM = 2010       # a home built since then would show a resale in the records if it had one
TYPICAL_STAY_YEARS = 10
MIN_SALE_PRICE = 50_000   # anything lower is a lease or a parking spot
SIZE_TOLERANCE = 0.25


def type_group(text: Optional[str]) -> Optional[str]:
    """The index's home type for a site's property type text."""
    t = (text or "").lower()
    if any(k in t for k in ("apt", "apartment", "co-op", "co-ownership", "loft")):
        return "apartment"
    if any(k in t for k in ("town", "row")):
        return "townhouse"
    if any(k in t for k in ("semi", "link")):
        return "semi"
    if any(k in t for k in ("detached", "single family", "house")):
        return "detached"
    return None


def level(year: int, group: Optional[str]) -> float:
    year = min(max(year, min(PRICE_LEVEL)), max(PRICE_LEVEL))
    return PRICE_LEVEL[year][TYPES.index(group or "composite")]


def parse_sqft(text) -> Optional[int]:
    """Square feet from '1000-1199', '1,350' or a number; the middle of a range."""
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", str(text or ""))]
    nums = [n for n in nums if 200 <= n <= 20_000]
    return round(sum(nums) / len(nums)) if nums else None


def building_key(address: Optional[str]) -> Optional[str]:
    """Street number and the first word of the street name: '711 Rossland Rd E' -> '711 rossland'."""
    words = re.findall(r"[a-z0-9]+", (address or "").lower())
    return " ".join(words[:2]) if len(words) >= 2 and words[0].isdigit() else None


@dataclass(frozen=True)
class Sale:
    sold_date: str
    price: float
    city: Optional[str]
    address: Optional[str]
    group: Optional[str]
    beds: Optional[int]
    sqft: Optional[int]

    @property
    def today_price(self) -> float:
        return self.price / level(int(self.sold_date[:4]), self.group)


@dataclass(frozen=True)
class Home:
    city: Optional[str]
    address: Optional[str]
    group: Optional[str]
    beds: Optional[int]
    sqft: Optional[int]
    year_built: Optional[int]
    group_inferred: bool = False


@dataclass(frozen=True)
class PurchaseEstimate:
    year: int
    price: float
    year_basis: str   # why that year
    comparables: int
    scope: str        # where the comparable sales came from
    type_basis: Optional[str] = None  # set when the home type was inferred

    def to_dict(self) -> dict:
        return {"year": self.year, "price": self.price, "year_basis": self.year_basis,
                "comparables": self.comparables, "scope": self.scope, "type_basis": self.type_basis}


class TypeGuesser:
    """Infers a home's type when the listing doesn't say, from the tracked listings that do:
    the most common type among the 7 most alike in condo status (a fee or unit number), size,
    bedrooms and bathrooms. Checked against the typed listings, it is right about 4 times in 5;
    most misses are condo townhouses versus apartments, which overlap in size."""

    NEIGHBOURS = 7

    def __init__(self, homes: Iterable[tuple]):
        """homes: (unit, fee, beds, baths, sqft, property type text) for each listing."""
        self._known = [(self._features(*h[:5]), type_group(h[5])) for h in homes if type_group(h[5])]

    @staticmethod
    def _features(unit, fee, beds, baths, sqft) -> tuple:
        condo = 1.0 if (fee and fee > 0) or unit else 0.0
        # condo status weighs most; 400 sq ft counts about as much as one bedroom or bathroom
        return (condo * 3, (sqft or 1200) / 400, beds or 2, baths or 2)

    def guess(self, unit, fee, beds, baths, sqft) -> Optional[str]:
        if not self._known or not beds or not (sqft or baths):
            return None
        import math
        from collections import Counter
        here = self._features(unit, fee, beds, baths, sqft)
        nearest = sorted(self._known, key=lambda k: math.dist(here, k[0]))[:self.NEIGHBOURS]
        return Counter(group for _, group in nearest).most_common(1)[0][0]


def _close_in_size(a: Optional[int], b: Optional[int]) -> bool:
    return not a or not b or abs(a - b) <= SIZE_TOLERANCE * a


def value_today(home: Home, sales: Iterable[Sale], exclude: frozenset = frozenset()):
    """(value in today's money, number of sales used, where they came from), or None."""
    pool = [s for s in sales if s.price >= MIN_SALE_PRICE and (s.sold_date, s.price) not in exclude]
    city = (home.city or "").lower()
    same_type = lambda s: not home.group or not s.group or s.group == home.group
    sized = lambda s: _close_in_size(home.sqft, s.sqft)
    in_city = lambda s: city and (s.city or "").lower() == city
    bkey = building_key(home.address)

    stages = [
        ("this building", 2, lambda s: bkey and building_key(s.address) == bkey
            and (not city or not s.city or in_city(s)) and same_type(s) and sized(s)),
        (f"similar homes in {home.city}", 5, lambda s: in_city(s) and s.group == home.group
            and (not home.beds or s.beds == home.beds) and sized(s)),
        (f"similar homes in {home.city}", 5, lambda s: in_city(s) and s.group == home.group and sized(s)),
        ("similar homes in Durham", 5, lambda s: s.group == home.group and sized(s)),
        ("similar homes in Durham", 5, lambda s: s.group == home.group),
    ]
    for scope, minimum, keep in stages:
        if not home.group and scope != "this building":
            break  # without a home type, only the building's own sales are a fair comparison
        comps = [s for s in pool if keep(s)]
        if len(comps) < minimum:
            continue
        per_sqft = [s.today_price / s.sqft for s in comps if s.sqft]
        if home.sqft and len(per_sqft) >= minimum:
            return statistics.median(per_sqft) * home.sqft, len(comps), scope
        return statistics.median(s.today_price for s in comps), len(comps), scope
    return None


def assumed_purchase_year(year_built: Optional[int], this_year: Optional[int] = None) -> tuple[int, str]:
    this_year = this_year or date.today().year
    if year_built and RECORDS_FROM <= year_built <= this_year:
        return year_built, f"Built {year_built}, so likely bought new"
    return this_year - TYPICAL_STAY_YEARS, f"Assumes about {TYPICAL_STAY_YEARS} years of ownership"


def estimate(home: Home, sales: Iterable[Sale], exclude: frozenset = frozenset(),
             year: Optional[int] = None, this_year: Optional[int] = None) -> Optional[PurchaseEstimate]:
    """What the seller likely paid. `year` overrides the assumed purchase year (for testing)."""
    valued = value_today(home, sales, exclude)
    if not valued:
        return None
    today_value, n, scope = valued
    if year is None:
        year, basis = assumed_purchase_year(home.year_built, this_year)
    else:
        basis = f"Bought {year}"
    type_basis = (f"Type inferred as {home.group} from size, bedrooms and bathrooms"
                  if home.group_inferred else None)
    return PurchaseEstimate(year, round(today_value * level(year, home.group), -3), basis, n, scope, type_basis)


# --- Reading it from the database ---

def sales_pool() -> list[Sale]:
    from listingtracker.db import get_area_sales
    return [Sale(r["sold_date"], r["sold_price"], r["city"], r["address"], type_group(r["type"]),
                 r["beds"], r["sqft"]) for r in get_area_sales()]


def type_guesser(listings=None, snapshots=None) -> TypeGuesser:
    """A TypeGuesser trained on every tracked listing whose type is known."""
    from listingtracker.db import get_all_latest_snapshots, get_all_listings
    listings = listings if listings is not None else get_all_listings(active_only=True)
    snapshots = snapshots if snapshots is not None else get_all_latest_snapshots()
    return TypeGuesser(
        (lst.unit, s.maintenance_fee, s.beds, s.baths, s.sqft_max or s.sqft_min, s.property_type)
        for lst in listings if (s := snapshots.get(lst.id))
    )


def home_for(listing, snap, guesser: Optional[TypeGuesser] = None) -> Home:
    """The home as the estimator sees it. With a guesser, a missing type is inferred, but only
    for a listing with a city: one without is not a single home that comparisons could value."""
    group = type_group(snap.property_type) if snap else None
    inferred = False
    if not group and snap and guesser and listing.city:
        group = guesser.guess(listing.unit, snap.maintenance_fee, snap.beds, snap.baths,
                              snap.sqft_max or snap.sqft_min)
        inferred = group is not None
    return Home(
        city=listing.city or None,
        address=listing.address or None,
        group=group,
        beds=snap.beds if snap else None,
        sqft=(snap.sqft_max or snap.sqft_min) if snap else None,
        year_built=snap.year_built if snap else None,
        group_inferred=inferred,
    )

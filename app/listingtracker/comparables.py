"""Comparable property tracker — finds similar-but-pricier properties for favorites.

For each favorited listing, finds properties in the same city with similar
characteristics (beds, baths, sqft) but at a higher price. Tracks their price
history and predicts when they might drop to within ±$25k of the favorite.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from listingtracker.models import Listing, Snapshot, Status

logger = logging.getLogger(__name__)

PRICE_TOLERANCE = 25_000  # ±$25k window

# How similar sqft must be (within 20%)
SQFT_TOLERANCE = 0.20

# Maximum price premium to track (200% of favorite's price)
MAX_PRICE_RATIO = 2.0


@dataclass
class Comparable:
    """A comparable property linked to a favorite."""
    id: Optional[int] = None
    favorite_id: int = 0
    listing_id: int = 0
    similarity_score: float = 0.0
    first_seen: Optional[str] = None
    last_checked: Optional[str] = None
    predicted_target_date: Optional[str] = None
    price_trend_pct_per_month: Optional[float] = None
    notes: str = ""


@dataclass
class ComparableDetail:
    """Comparable enriched with listing + snapshot data for display."""
    comparable: Comparable
    listing: Listing
    snapshot: Snapshot
    favorite_price: float
    price_gap: float
    monthly_trend: Optional[float] = None
    months_to_target: Optional[float] = None
    predicted_date: Optional[str] = None
    price_history: list = None

    def __post_init__(self):
        if self.price_history is None:
            self.price_history = []


def find_comparables_for_favorite(
    favorite: Listing,
    fav_snap: Snapshot,
    all_listings: list[Listing],
    all_snapshots: dict[int, Snapshot],
) -> list[tuple[int, float]]:
    """Find listing IDs similar to `favorite` but more expensive.

    Returns list of (listing_id, similarity_score) sorted by score descending.
    """
    if not fav_snap or not fav_snap.list_price or fav_snap.list_price <= 0:
        return []

    fav_price = fav_snap.list_price
    fav_beds = fav_snap.beds or 0
    fav_baths = fav_snap.baths or 0
    fav_sqft = fav_snap.sqft_max or fav_snap.sqft_min or 0
    fav_city = favorite.city
    fav_type = fav_snap.property_type or ""

    results = []
    for listing in all_listings:
        if listing.id == favorite.id:
            continue
        if listing.city != fav_city:
            continue

        snap = all_snapshots.get(listing.id)
        if not snap or not snap.list_price or snap.list_price <= 0:
            continue
        if snap.status != Status.FOR_SALE:
            continue

        price = snap.list_price

        # Must be MORE expensive than the favorite
        if price <= fav_price:
            continue
        # But not absurdly more
        if price > fav_price * MAX_PRICE_RATIO:
            continue

        # Match beds (exact)
        if fav_beds and snap.beds and snap.beds != fav_beds:
            continue

        # Score the similarity
        score = _similarity_score(
            fav_price, fav_beds, fav_baths, fav_sqft, fav_type,
            price, snap.beds, snap.baths,
            snap.sqft_max or snap.sqft_min or 0,
            snap.property_type or "",
        )

        if score >= 40:
            results.append((listing.id, score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:20]


def _similarity_score(
    fav_price: float, fav_beds: int, fav_baths: int, fav_sqft: int, fav_type: str,
    comp_price: float, comp_beds: int, comp_baths: int, comp_sqft: int, comp_type: str,
) -> float:
    """Score 0-100 how similar a comparable is to the favorite (ignoring price direction)."""
    score = 0.0

    # Beds match (exact required, already filtered, but score it)
    if fav_beds and comp_beds and fav_beds == comp_beds:
        score += 25

    # Baths closeness
    if fav_baths and comp_baths:
        if fav_baths == comp_baths:
            score += 20
        elif abs(fav_baths - comp_baths) == 1:
            score += 10

    # Sqft closeness
    if fav_sqft and comp_sqft and fav_sqft > 0:
        sqft_diff_pct = abs(comp_sqft - fav_sqft) / fav_sqft
        if sqft_diff_pct <= 0.05:
            score += 25
        elif sqft_diff_pct <= 0.10:
            score += 20
        elif sqft_diff_pct <= SQFT_TOLERANCE:
            score += 15
        elif sqft_diff_pct <= 0.30:
            score += 8

    # Property type match
    if fav_type and comp_type:
        if fav_type.lower() == comp_type.lower():
            score += 15
        elif _types_similar(fav_type, comp_type):
            score += 8

    # Price proximity bonus — closer price gap = more interesting
    price_gap_pct = (comp_price - fav_price) / fav_price
    if price_gap_pct <= 0.10:
        score += 15
    elif price_gap_pct <= 0.20:
        score += 10
    elif price_gap_pct <= 0.35:
        score += 5

    return min(100, score)


def _types_similar(a: str, b: str) -> bool:
    condo_terms = {"condo", "condo apartment", "condominium", "apt", "apartment"}
    a_low, b_low = a.lower().strip(), b.lower().strip()
    return a_low in condo_terms and b_low in condo_terms


def analyze_price_trend(snapshots: list[Snapshot]) -> Optional[float]:
    """Calculate monthly price trend from snapshot history.

    Returns percentage change per month (negative = dropping).
    Needs at least 2 snapshots with valid prices spanning > 7 days.
    """
    priced = [
        (s.fetched_at, s.list_price)
        for s in snapshots
        if s.list_price and s.list_price > 0 and s.fetched_at
    ]
    if len(priced) < 2:
        return None

    priced.sort(key=lambda x: x[0])

    first_date = _parse_date(priced[0][0])
    last_date = _parse_date(priced[-1][0])
    if not first_date or not last_date:
        return None

    days = (last_date - first_date).days
    if days < 7:
        return None

    months = days / 30.44
    if months < 0.25:
        return None

    first_price = priced[0][1]
    last_price = priced[-1][1]

    if first_price <= 0:
        return None

    total_change_pct = ((last_price - first_price) / first_price) * 100
    return total_change_pct / months


def predict_target_date(
    current_price: float,
    target_price: float,
    monthly_trend_pct: float,
    tolerance: float = PRICE_TOLERANCE,
) -> Optional[str]:
    """Predict when price will drop into target ± tolerance range.

    Returns ISO date string, or None if trend doesn't lead there.
    """
    if monthly_trend_pct >= 0:
        return None

    target_high = target_price + tolerance
    if current_price <= target_high:
        return None

    gap = current_price - target_high
    monthly_drop = abs(monthly_trend_pct / 100) * current_price

    if monthly_drop <= 0:
        return None

    months_needed = gap / monthly_drop
    if months_needed > 36:
        return None

    target_date = datetime.now() + timedelta(days=months_needed * 30.44)
    return target_date.strftime("%Y-%m-%d")


def _parse_date(val) -> Optional[datetime]:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except (ValueError, TypeError):
            return None
    return None

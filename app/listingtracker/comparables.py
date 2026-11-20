"""Comparable property tracker for favorites.

For each favorited listing, finds properties in the same city with at least as many bedrooms:
  * cheaper ones that are at least as big (bedrooms, bathrooms, and nearly the floor area), which
    are simply better value, so there is no price floor for them;
  * similar but pricier ones, with a forecast from their price history of when they might drop
    to within $25k of the favorite.
"""

from __future__ import annotations

import logging

from listingtracker.models import Listing, Snapshot, Status

logger = logging.getLogger(__name__)

PRICE_TOLERANCE = 25_000  # ±$25k window

# How similar sqft must be (within 20%)
SQFT_TOLERANCE = 0.20

# Maximum price premium to track (200% of favorite's price)
MAX_PRICE_RATIO = 2.0

# A cheaper listing counts when its floor area is at least this share of the favorite's
SQFT_MARGIN = 0.95
MIN_PRICE = 50_000   # below this a "listing" is a parking spot, a locker or a lease
MAX_CHEAPER = 10
MAX_PRICIER = 20


def at_least_as_big(fav_beds, fav_baths, fav_sqft, beds, baths, sqft) -> bool:
    """Same or more bedrooms and bathrooms, and the floor area or within a small margin of it.
    A size the favorite doesn't state can't rule a listing out; one the listing doesn't state can't
    show it's as big."""
    if fav_beds and (beds or 0) < fav_beds:
        return False
    if fav_baths and (baths or 0) < fav_baths:
        return False
    if fav_sqft and (sqft or 0) < fav_sqft * SQFT_MARGIN:
        return False
    return True


def find_comparables_for_favorite(
    favorite: Listing,
    fav_snap: Snapshot,
    all_listings: list[Listing],
    all_snapshots: dict[int, Snapshot],
) -> list[tuple[int, float]]:
    """Listing IDs worth comparing with `favorite`: cheaper ones at least as big, then similar
    pricier ones. Returns (listing_id, similarity_score), each group by score, best first."""
    if not fav_snap or not fav_snap.list_price or fav_snap.list_price <= 0:
        return []

    fav_price = fav_snap.list_price
    fav_beds = fav_snap.beds or 0
    fav_baths = fav_snap.baths or 0
    fav_sqft = fav_snap.sqft_max or fav_snap.sqft_min or 0
    fav_city = favorite.city
    fav_type = fav_snap.property_type or ""

    cheaper, pricier = [], []
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
        sqft = snap.sqft_max or snap.sqft_min or 0
        if price < MIN_PRICE or price > fav_price * MAX_PRICE_RATIO:
            continue
        # Fewer bedrooms is a smaller home at any price; more is never a reason to leave one out
        if fav_beds and snap.beds and snap.beds < fav_beds:
            continue

        score = _similarity_score(
            fav_price, fav_beds, fav_baths, fav_sqft, fav_type,
            price, snap.beds, snap.baths, sqft, snap.property_type or "",
        )
        if price <= fav_price:
            if at_least_as_big(fav_beds, fav_baths, fav_sqft, snap.beds, snap.baths, sqft):
                cheaper.append((listing.id, score))
        elif score >= 40:
            pricier.append((listing.id, score))

    cheaper.sort(key=lambda x: x[1], reverse=True)
    pricier.sort(key=lambda x: x[1], reverse=True)
    return cheaper[:MAX_CHEAPER] + pricier[:MAX_PRICIER]


def _similarity_score(
    fav_price: float, fav_beds: int, fav_baths: int, fav_sqft: int, fav_type: str,
    comp_price: float, comp_beds: int, comp_baths: int, comp_sqft: int, comp_type: str,
) -> float:
    """Score 0-100 how similar a comparable is to the favorite (ignoring price direction)."""
    score = 0.0

    # Beds: the same, or more (fewer is filtered out)
    if fav_beds and comp_beds:
        if fav_beds == comp_beds:
            score += 25
        elif comp_beds > fav_beds:
            score += 20

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

    # Price proximity bonus — closer price gap = more interesting; a lower price is in range
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


def scan_comparables(favorites: list[Listing]) -> tuple[int, list[str]]:
    """Find and store comparables for each favorite, with each one's price trend and the date
    its forecast cuts would bring it into range. Returns (comparables stored, in-range alerts)."""
    from listingtracker import pricehistory
    from listingtracker.db import (
        get_all_listings, get_all_latest_snapshots, get_latest_snapshot,
        get_snapshots_for_listing, upsert_comparable, delete_stale_comparables,
    )

    all_listings = get_all_listings(active_only=True)
    by_id = {lst.id: lst for lst in all_listings}
    all_snapshots = get_all_latest_snapshots()
    market = pricehistory.market_pattern()
    count, alerts = 0, []

    for fav in favorites:
        fav_snap = all_snapshots.get(fav.id) or get_latest_snapshot(fav.id)
        if not fav_snap or not fav_snap.list_price:
            continue
        target = fav_snap.list_price + PRICE_TOLERANCE

        for listing_id, score in find_comparables_for_favorite(fav, fav_snap, all_listings, all_snapshots):
            comp_snap = all_snapshots[listing_id]
            summary = pricehistory.summarize(listing_id, get_snapshots_for_listing(listing_id), market)
            trend = pricehistory.trend_pct_per_month(summary.points)
            gap = comp_snap.list_price - fav_snap.list_price

            predicted, notes = None, ""
            if gap <= 0:
                notes = f"${-gap:,.0f} less, and at least as big"
            elif gap <= PRICE_TOLERANCE:
                notes = f"IN RANGE! Gap: ${gap:,.0f}"
                alerts.append(
                    f"{by_id[listing_id].address}: now ${comp_snap.list_price:,.0f}, "
                    f"${gap:,.0f} above your ${fav_snap.list_price:,.0f} favorite"
                )
            elif summary.forecast and summary.forecast.confidence != "low":
                # Only a listing that has cut before has a pace of its own worth projecting
                when = pricehistory.target_date(comp_snap.list_price, target, summary.forecast)
                if when:
                    predicted = when.isoformat()
                    notes = f"Forecast cuts may reach your range by {predicted}"

            upsert_comparable(fav.id, listing_id, score, predicted_target_date=predicted,
                              price_trend_pct_per_month=trend, notes=notes)
            count += 1

    stale = delete_stale_comparables()
    logger.info("Comparables: %d tracked, %d stale removed", count, stale)
    return count, alerts

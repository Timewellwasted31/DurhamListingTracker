"""ELO-style value score for comparing listings against a buyer profile.

Each metric is normalized to a 0–100 sub-score, then weighted and combined
into a composite score (0–100).  Higher = better value proposition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from listingtracker.finance import (
    FinanceConfig, price_per_sqft, fee_per_sqft, monthly_carrying_cost,
)


@dataclass
class ValueMetrics:
    price_psf: Optional[float] = None
    fee_psf: Optional[float] = None
    tax_psf: Optional[float] = None
    monthly_cost: Optional[float] = None
    amenity_cost: Optional[float] = None
    score: Optional[float] = None

    # Sub-scores (0–100, higher = better)
    price_psf_score: float = 0
    fee_psf_score: float = 0
    tax_psf_score: float = 0
    monthly_score: float = 0
    amenity_score: float = 0
    size_score: float = 0
    bedroom_score: float = 0


# Anchors for the Durham Region condo market — based on typical ranges.
# Each tuple is (excellent_low, acceptable_high): below low → 100, above high → 0.
ANCHORS = {
    "price_psf": (350, 700),
    "fee_psf": (0.30, 1.20),
    "tax_psf": (2.0, 6.0),
    "monthly": (1200, 2800),
    "amenity": (0.0, 0.60),
    "sqft": (400, 1200),
    "beds": (0, 3),
}

# Weights for the composite score (must sum to 1.0)
WEIGHTS = {
    "price_psf": 0.25,
    "fee_psf": 0.15,
    "tax_psf": 0.10,
    "monthly": 0.20,
    "amenity": 0.10,
    "size": 0.10,
    "bedroom": 0.10,
}


def _linear_score(value: float, best: float, worst: float) -> float:
    """Map value into 0–100 where best→100 and worst→0, clamped."""
    if best == worst:
        return 50.0
    raw = (worst - value) / (worst - best) * 100
    return max(0.0, min(100.0, raw))


def _inverse_score(value: float, best: float, worst: float) -> float:
    """For metrics where higher is better (sqft, beds)."""
    if best == worst:
        return 50.0
    raw = (value - best) / (worst - best) * 100
    return max(0.0, min(100.0, raw))


def _sqft_mid(sqft_min: Optional[int], sqft_max: Optional[int]) -> Optional[float]:
    if sqft_min and sqft_max:
        return (sqft_min + sqft_max) / 2
    return sqft_min or sqft_max or None


def compute_value_metrics(
    list_price: Optional[float],
    maintenance_fee: Optional[float],
    taxes_annual: Optional[float],
    fee_includes: str,
    sqft_min: Optional[int],
    sqft_max: Optional[int],
    beds: Optional[int],
    cfg: FinanceConfig,
) -> ValueMetrics:
    m = ValueMetrics()

    sqft = _sqft_mid(sqft_min, sqft_max)
    mf = maintenance_fee or 0

    if list_price and sqft and sqft > 0:
        m.price_psf = list_price / sqft
    if mf > 0 and sqft and sqft > 0:
        m.fee_psf = mf / sqft
    if taxes_annual and taxes_annual > 0 and sqft and sqft > 0:
        m.tax_psf = taxes_annual / sqft

    if list_price:
        m.monthly_cost = monthly_carrying_cost(
            list_price, mf, taxes_annual or 0, fee_includes, cfg
        )

    if mf > 0 and m.fee_psf is not None:
        includes = {i.strip().lower() for i in (fee_includes or "").split(",") if i.strip()}
        basics = {"heat", "water", "gas", "hydro", "electricity", "heating"}
        included_count = len(basics & includes)
        if included_count == 0:
            m.amenity_cost = m.fee_psf
        else:
            utility_fraction = min(0.40, included_count * 0.10)
            m.amenity_cost = m.fee_psf * (1 - utility_fraction)

    # Sub-scores
    if m.price_psf is not None:
        m.price_psf_score = _linear_score(m.price_psf, *ANCHORS["price_psf"])
    if m.fee_psf is not None:
        m.fee_psf_score = _linear_score(m.fee_psf, *ANCHORS["fee_psf"])
    else:
        m.fee_psf_score = 70.0  # no fee is good (freehold)
    if m.tax_psf is not None:
        m.tax_psf_score = _linear_score(m.tax_psf, *ANCHORS["tax_psf"])
    if m.monthly_cost is not None:
        m.monthly_score = _linear_score(m.monthly_cost, *ANCHORS["monthly"])
    if sqft:
        m.size_score = _inverse_score(sqft, *ANCHORS["sqft"])
    if beds is not None:
        m.bedroom_score = _inverse_score(beds, *ANCHORS["beds"])

    # Composite
    weighted = (
        m.price_psf_score * WEIGHTS["price_psf"]
        + m.fee_psf_score * WEIGHTS["fee_psf"]
        + m.tax_psf_score * WEIGHTS["tax_psf"]
        + m.monthly_score * WEIGHTS["monthly"]
        + m.amenity_score * WEIGHTS["amenity"]
        + m.size_score * WEIGHTS["size"]
        + m.bedroom_score * WEIGHTS["bedroom"]
    )

    # Penalty: if we're missing key data, reduce confidence
    data_points = sum([
        m.price_psf is not None,
        m.fee_psf is not None or mf == 0,
        m.tax_psf is not None,
        m.monthly_cost is not None,
        sqft is not None,
    ])
    confidence = data_points / 5
    m.score = round(weighted * confidence, 1)

    # Amenity sub-score uses the amenity anchor
    if m.amenity_cost is not None:
        m.amenity_score = _linear_score(m.amenity_cost, *ANCHORS["amenity"])
        # Recompute with amenity
        weighted = (
            m.price_psf_score * WEIGHTS["price_psf"]
            + m.fee_psf_score * WEIGHTS["fee_psf"]
            + m.tax_psf_score * WEIGHTS["tax_psf"]
            + m.monthly_score * WEIGHTS["monthly"]
            + m.amenity_score * WEIGHTS["amenity"]
            + m.size_score * WEIGHTS["size"]
            + m.bedroom_score * WEIGHTS["bedroom"]
        )
        m.score = round(weighted * confidence, 1)

    return m

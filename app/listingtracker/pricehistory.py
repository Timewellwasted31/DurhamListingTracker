"""A listing's asking-price history, and a forecast of its next price cut.

The listing sites publish the date a home was listed and every asking price since; our own
fetches add a change the site's record doesn't show yet. The forecast follows the listing's own
rhythm of cuts and leans on how the other tracked listings have behaved when it has few of its own.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from listingtracker.models import Snapshot, Status

logger = logging.getLogger(__name__)

Point = tuple[date, float]

# Used until enough tracked listings have cut their price to learn from
DEFAULT_FIRST_CUT_DAYS = 45
DEFAULT_NEXT_CUT_DAYS = 30
DEFAULT_CUT_PCT = 0.04
MIN_MARKET_SAMPLES = 5
MIN_SPREAD_DAYS = 7
HORIZON_DAYS = 3 * 365  # don't project target dates further out than this


def _day(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def timeline(site_points: Iterable[tuple[str, float]], snapshots: Iterable[Snapshot]) -> list[Point]:
    """Asking prices over time, one entry per change, oldest first.

    The site's record is used as is. A price we saw ourselves only counts after the site's last
    entry, so a page fetched a day before the site caught up can't add a false change."""
    points = sorted((d, p) for d, p in ((_day(d), p) for d, p in site_points) if d and p)
    after = points[-1][0] if points else date.min
    seen = sorted(
        (d, s.list_price) for d, s in ((_day(s.fetched_at), s) for s in snapshots)
        if d and d > after and s.list_price
    )
    changes: list[Point] = []
    for d, p in points + seen:
        if not changes or p != changes[-1][1]:
            changes.append((d, p))
    return changes


def days_on_market(list_date: Optional[str], snap: Optional[Snapshot], today: Optional[date] = None) -> Optional[int]:
    """Days since listing: up to today while for sale, up to the last check otherwise."""
    start = _day(list_date)
    if not start:
        return snap.days_on_market if snap else None
    end = today or date.today()
    if snap and snap.status not in (Status.FOR_SALE, Status.UNKNOWN):
        end = _day(snap.fetched_at) or end
    return max(0, (end - start).days)


@dataclass(frozen=True)
class Cut:
    date: date
    days: int    # since the previous price change, or since listing for the first cut
    pct: float   # fraction of the old price taken off


def cuts(points: list[Point]) -> list[Cut]:
    out = []
    for (d0, p0), (d1, p1) in zip(points, points[1:]):
        if p1 < p0:
            out.append(Cut(d1, (d1 - d0).days, (p0 - p1) / p0))
    return out


@dataclass(frozen=True)
class MarketPattern:
    """How tracked listings cut their prices: days to the first cut, days between later cuts,
    and the size of each cut."""
    first_days: list[int]
    next_days: list[int]
    pcts: list[float]

    @classmethod
    def from_histories(cls, histories: Iterable[list[Point]]) -> "MarketPattern":
        first, nxt, pcts = [], [], []
        for points in histories:
            for i, c in enumerate(cuts(points)):
                (first if i == 0 else nxt).append(c.days)
                pcts.append(c.pct)
        return cls(first, nxt, pcts)

    @staticmethod
    def _median(values, default):
        return statistics.median(values) if len(values) >= MIN_MARKET_SAMPLES else default

    def gap(self, first: bool) -> float:
        return (self._median(self.first_days, DEFAULT_FIRST_CUT_DAYS) if first
                else self._median(self.next_days, DEFAULT_NEXT_CUT_DAYS))

    def spread(self, first: bool) -> float:
        values = self.first_days if first else self.next_days
        if len(values) < MIN_MARKET_SAMPLES:
            return self.gap(first) / 2
        q1, _, q3 = statistics.quantiles(values, n=4)
        return (q3 - q1) / 2

    def pct(self) -> float:
        return self._median(self.pcts, DEFAULT_CUT_PCT)


@dataclass(frozen=True)
class DropForecast:
    expected: date
    earliest: date
    latest: date
    pct: float              # expected size of the cut, as a fraction
    price: float            # expected asking price after it
    next_gap_days: float    # typical wait between later cuts, for projecting further ahead
    confidence: str         # high / medium / low
    overdue: bool
    basis: str              # one sentence on what the forecast rests on

    def to_dict(self) -> dict:
        return {
            "expected": self.expected.isoformat(),
            "earliest": self.earliest.isoformat(),
            "latest": self.latest.isoformat(),
            "pct": round(self.pct * 100, 1),
            "price": self.price,
            "confidence": self.confidence,
            "overdue": self.overdue,
            "basis": self.basis,
        }


def _blend(own: list[float], weights: list[float], market: float) -> float:
    """Weighted mean of the listing's own values, pulled toward the market's by one sample's worth."""
    if not own:
        return market
    own_mean = sum(v * w for v, w in zip(own, weights)) / sum(weights)
    return (own_mean * len(own) + market) / (len(own) + 1)


def forecast(points: list[Point], status: Status, market: MarketPattern,
             today: Optional[date] = None) -> Optional[DropForecast]:
    """When the next price cut is likely, and to what price. None if the home isn't for sale."""
    if status != Status.FOR_SALE or not points:
        return None
    today = today or date.today()
    own = cuts(points)
    gaps = [c.days for c in own]
    recency = list(range(1, len(own) + 1))  # later cuts say more about the seller now
    first = not own

    gap = _blend(gaps, recency, market.gap(first))
    next_gap = _blend(gaps, recency, market.gap(False))
    pct = _blend([c.pct for c in own], [1] * len(own), market.pct())
    spread = max(MIN_SPREAD_DAYS, statistics.pstdev(gaps) if len(gaps) >= 2 else market.spread(first))

    last_change, price = points[-1]
    expected = last_change + timedelta(days=round(gap))
    overdue = expected < today
    if overdue:
        expected = today
    earliest = max(today, expected - timedelta(days=round(spread)))
    latest = expected + timedelta(days=round(spread))

    if len(gaps) >= 3 and statistics.pstdev(gaps) <= 0.5 * statistics.mean(gaps):
        confidence = "high"
    elif own:
        confidence = "medium"
    else:
        confidence = "low"

    if len(own) >= 2:
        basis = (f"Cut {len(own)} times since listing, about every {round(statistics.mean(gaps))} days, "
                 f"by {statistics.mean(c.pct for c in own) * 100:.1f}% on average.")
    elif own:
        basis = (f"Cut once, {own[0].days} days after listing, by {own[0].pct * 100:.1f}%. "
                 f"Tracked listings usually cut again about {round(market.gap(False))} days later.")
    else:
        basis = (f"No cuts in {(today - points[0][0]).days} days. Tracked listings that cut usually "
                 f"do so about {round(market.gap(True))} days after listing.")
    if overdue:
        basis += " It has gone longer than usual without a cut."

    return DropForecast(
        expected=expected, earliest=earliest, latest=latest,
        pct=pct, price=round(price * (1 - pct), -3), next_gap_days=next_gap,
        confidence=confidence, overdue=overdue, basis=basis,
    )


def target_date(price: float, target: float, fc: Optional[DropForecast]) -> Optional[date]:
    """When cuts at the forecast pace would bring `price` down to `target`, if within the horizon."""
    if not fc or price <= target:
        return None
    when = fc.expected
    horizon = when + timedelta(days=HORIZON_DAYS)
    while when <= horizon:
        price *= 1 - fc.pct
        if price <= target:
            return when
        when += timedelta(days=round(fc.next_gap_days))
    return None


def trend_pct_per_month(points: list[Point], today: Optional[date] = None) -> Optional[float]:
    """Average monthly change in asking price since listing (negative when falling)."""
    if not points:
        return None
    days = ((today or date.today()) - points[0][0]).days
    if days < 7:
        return None
    first, last = points[0][1], points[-1][1]
    return (last - first) / first * 100 / (days / 30.44)


# --- Reading it all for a listing ---

@dataclass(frozen=True)
class PriceSummary:
    list_date: Optional[str]
    points: list[Point]
    days_on_market: Optional[int]
    forecast: Optional[DropForecast]


def market_pattern() -> MarketPattern:
    from listingtracker.db import get_price_histories
    return MarketPattern.from_histories(
        timeline(points, ()) for _, points in get_price_histories().values()
    )


def summarize(listing_id: int, snapshots: list[Snapshot], market: Optional[MarketPattern] = None) -> PriceSummary:
    """Price history, days on market and next-cut forecast for one listing."""
    from listingtracker.db import get_price_histories

    list_date, site_points = get_price_histories(listing_id).get(listing_id, (None, []))
    points = timeline(site_points, snapshots)
    snap = snapshots[-1] if snapshots else None
    fc = forecast(points, snap.status, market or market_pattern()) if snap else None
    return PriceSummary(list_date, points, days_on_market(list_date, snap), fc)


def backfill_from_cache() -> int:
    """Fill site price history from listing pages already saved on disk. It runs only while no
    history is stored at all, i.e. once, for a database from before history was recorded."""
    import gzip
    from listingtracker import db
    from listingtracker.fetcher import get_adapter_for_url
    from listingtracker.paths import HTML_CACHE_DIR

    if db.has_price_history():
        return 0
    pages = {p.name.split(".")[0]: p for p in HTML_CACHE_DIR.glob("*/*.html.gz")}
    newest = {}  # listing id -> (url, newest saved page)
    for snap_id, listing_id, url in db.get_snapshot_ids():
        if str(snap_id) in pages:
            newest[listing_id] = (url, pages[str(snap_id)])

    filled = 0
    for listing_id, (url, path) in newest.items():
        adapter = get_adapter_for_url(url)
        if not adapter:
            continue
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                parsed = adapter.parse(f.read(), url=url)
        except Exception as e:  # one unreadable page shouldn't stop the rest
            logger.debug("Backfill skipped %s: %s", path, e)
            continue
        if parsed.get("list_date") or parsed.get("price_history"):
            db.save_price_history(listing_id, parsed.get("list_date"), parsed.get("price_history") or [])
            filled += 1
    logger.info("Price history backfilled for %d listings from saved pages", filled)
    return filled


def backfill_in_background() -> None:
    import threading
    threading.Thread(target=backfill_from_cache, name="price-history-backfill", daemon=True).start()

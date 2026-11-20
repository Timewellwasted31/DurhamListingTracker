"""Past sales of other units in the same building, read from strata.ca with the user's own account.

Strata shows a building's full listing history, sold prices included, only to signed-in members.
The user signs in once in a browser window the app opens; that session is kept in the app's own
browser folder and reused, read-only, to look at building pages.

The account must not get flagged, so this reads like a person browsing, slowly, and stops early:
  * one building page per building, and not again for a week;
  * on it, the "Sold" filter and a few "Show more" clicks, a few seconds apart;
  * 30 to 60 seconds between buildings, and at most 25 buildings a run;
  * any sign of a block or a challenge ends the run and pauses reading for a day;
  * finding itself signed out ends the run too (the user signs in again).
"""

from __future__ import annotations

import json
import logging
import random
import re
import statistics
import time
from datetime import date, datetime, timedelta
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

from listingtracker import siteaccounts

logger = logging.getLogger(__name__)

SIGN_IN_URL = "https://strata.ca/"

RECHECK_DAYS = 7
MAX_BUILDINGS_PER_RUN = 25
PAUSE_BETWEEN_BUILDINGS = (30.0, 60.0)
PAUSE_BETWEEN_CLICKS = (3.0, 7.0)
MAX_SHOW_MORE = 4          # the page shows 8 more per click: a few years of sales in most buildings
YEARS_BACK = 3             # stop paging once the sales shown are older than this
PAUSE_AFTER_BLOCK = timedelta(hours=24)
SHOWN_MONTHS = 36          # how far back the app shows a building's sales

STATE_CHECKED = "building_sales_checked:"   # + building -> ISO date it was last read
STATE_PAUSED = "building_sales_paused_until"


class StopRun(Exception):
    """Reading must stop now: signed out, blocked or challenged."""


# --- Which buildings -------------------------------------------------------------------------

def building_of(url: str) -> Optional[str]:
    """Strata's building page path for a strata.ca listing URL: 'whitby/712-rossland-rd-e-...'."""
    u = urlparse(url or "")
    if not u.netloc.endswith("strata.ca"):
        return None
    parts = [p for p in u.path.split("/") if p]
    return "/".join(parts[:2]) if len(parts) >= 2 else None


def _is_multi_unit(listing, snap) -> bool:
    """Condos and condo townhouses have a building worth reading; a freehold house has not."""
    return bool(listing.unit) or bool(snap and (snap.maintenance_fee or 0) > 0)


def buildings_to_read(listings: Iterable, snapshots: dict, checked: dict[str, str],
                      today: Optional[date] = None, limit: int = MAX_BUILDINGS_PER_RUN) -> list[str]:
    """Buildings due a read, favourites' first, then the longest unread; at most `limit`."""
    today = today or date.today()
    due: dict[str, tuple] = {}
    for lst in listings:
        b = building_of(lst.url)
        if not b or not _is_multi_unit(lst, snapshots.get(lst.id)):
            continue
        last = checked.get(b)
        if last and (today - date.fromisoformat(last[:10])).days < RECHECK_DAYS:
            continue
        rank = (not lst.favorited, last or "")
        due[b] = min(due.get(b, rank), rank)
    return sorted(due, key=lambda b: due[b])[:limit]


# --- Reading the page's data -----------------------------------------------------------------

def parse_sales(body: dict, building: str) -> list[dict]:
    """The sold units in one SEARCH_LISTINGS_QUERY response."""
    listings = (((body or {}).get("data") or {}).get("listingsNew") or {}).get("listings") or []
    out = []
    for item in listings:
        pl = item.get("propertyListing") or {}
        if pl.get("propertyListingState") != "SOLD" or not pl.get("soldPrice") or not pl.get("soldDate"):
            continue
        price = float(pl["soldPrice"])
        if price < 50_000:  # a lease or a parking spot
            continue
        unit = pl.get("unit") or (pl.get("propertyUnitUid") or "").replace("unit-", "") or "?"
        out.append({
            "building": building,
            "unit": str(unit).upper(),
            "sold_date": pl["soldDate"][:10],
            "sold_price": price,
            "address": pl.get("address"),
            "list_price": float(pl["listPrice"]) if pl.get("listPrice") else None,
            "list_date": (pl.get("listDate") or "")[:10] or None,
            "beds": pl.get("bedroomCount"),
            "beds_plus": pl.get("bedroomPlusCount") or 0,
            "baths": pl.get("bathroomCount"),
            "sqft": pl.get("sqftSizeHigh") or pl.get("sqftSizeLow"),
            "parking": pl.get("parkingSpaces"),
            "fee": pl.get("maintenanceFees"),
            "taxes": pl.get("taxes"),
            "mls": pl.get("mlsNumber"),
        })
    return out


def _is_sales_query(post: Optional[str], building: str) -> bool:
    if not post or "SEARCH_LISTINGS_QUERY" not in post:
        return False
    try:
        f = (json.loads(post).get("variables") or {}).get("filter") or {}
    except ValueError:
        return False
    return "SOLD" in (f.get("propertyListingStates") or []) and building.split("/")[-1] in (f.get("propertyUids") or [])


_CHALLENGE = re.compile(r"just a moment|checking your browser|access denied|are you a robot|captcha|too many requests",
                        re.IGNORECASE)


# --- Reading, in the user's own browser signed in to strata.ca (see siteaccounts) -------------

def _pause(span: tuple[float, float], cancel: Optional[Callable[[], bool]] = None) -> None:
    end = time.monotonic() + random.uniform(*span)
    while time.monotonic() < end:
        if cancel and cancel():
            return
        time.sleep(0.5)


def _check_page(page, status: Optional[int]) -> None:
    if status in (401, 403, 429) or (status or 0) >= 500:
        raise StopRun(f"strata.ca answered {status}")
    title = page.title() or ""
    if _CHALLENGE.search(title):
        raise StopRun(f"strata.ca showed a check page ({title[:60]})")
    if page.get_by_text("Sign In | Join").count() or page.get_by_text("Account Required").count():
        raise StopRun("signed out of strata.ca")


def read_building(page, building: str, cancel=None) -> list[dict]:
    """Every sale the building's history shows within YEARS_BACK, newest first."""
    found: dict[tuple, dict] = {}
    blocked: list[int] = []

    def on_response(resp):
        if "slaterealty.ca/graphql" not in resp.url:
            return
        if resp.status in (401, 403, 429):
            blocked.append(resp.status)
            return
        if _is_sales_query(resp.request.post_data, building):
            try:
                for s in parse_sales(resp.json(), building):
                    found[(s["unit"], s["sold_date"])] = s
            except Exception as e:  # a reply we can't read is skipped, not fatal
                logger.debug("Unreadable sales reply for %s: %s", building, e)

    page.on("response", on_response)
    try:
        resp = page.goto(f"https://strata.ca/{building}", wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(random.uniform(3000, 6000))
        _check_page(page, resp.status if resp else None)
        status = page.locator('select[aria-label="Listing status"]')
        if not status.count():
            return []  # no history section: nothing sold here, or not a building page
        status.scroll_into_view_if_needed()
        page.wait_for_timeout(random.uniform(1000, 2500))
        status.select_option("SOLD")
        _pause(PAUSE_BETWEEN_CLICKS, cancel)
        cutoff = (date.today() - timedelta(days=365 * YEARS_BACK)).isoformat()
        for _ in range(MAX_SHOW_MORE):
            if blocked:
                raise StopRun(f"strata.ca answered {blocked[0]}")
            if cancel and cancel():
                break
            if found and min(s["sold_date"] for s in found.values()) < cutoff:
                break
            more = page.locator("#historic-listings").get_by_text("Show more", exact=True)
            if not more.count():
                break
            more.first.scroll_into_view_if_needed()
            page.wait_for_timeout(random.uniform(500, 1500))
            more.first.click()
            _pause(PAUSE_BETWEEN_CLICKS, cancel)
        if blocked:
            raise StopRun(f"strata.ca answered {blocked[0]}")
    finally:
        page.remove_listener("response", on_response)
    return sorted(found.values(), key=lambda s: s["sold_date"], reverse=True)


def paused_until() -> Optional[datetime]:
    from listingtracker.db import get_state
    value = get_state(STATE_PAUSED)
    until = datetime.fromisoformat(value) if value else None
    return until if until and until > datetime.now() else None


def needs_sign_in() -> bool:
    return not siteaccounts.is_signed_in("strata")


def refresh(progress: Optional[Callable[[int, int, str], None]] = None,
            cancel: Optional[Callable[[], bool]] = None) -> dict:
    """Read the sales of the buildings that are due. Returns {buildings, sales, stopped}, where
    `stopped` says why the run ended early, if it did."""
    from patchright.sync_api import sync_playwright
    from listingtracker.db import (get_all_latest_snapshots, get_all_listings, get_state,
                                   save_area_sales, save_building_sales, set_state)

    if needs_sign_in():
        return {"buildings": 0, "sales": 0, "stopped": "Sign in to strata.ca first"}
    until = paused_until()
    if until:
        return {"buildings": 0, "sales": 0,
                "stopped": f"Paused after a block until {until:%b %d %H:%M}, to protect the account"}

    listings = get_all_listings(active_only=True)
    snapshots = get_all_latest_snapshots()
    checked = {b: get_state(STATE_CHECKED + b) or "" for b in _all_buildings(listings)}
    buildings = buildings_to_read(listings, snapshots, checked)
    cities = {building_of(lst.url): lst.city for lst in listings if building_of(lst.url)}
    if not buildings:
        return {"buildings": 0, "sales": 0, "stopped": None}

    done = sales = 0
    stopped = None
    with siteaccounts.claim("strata"), sync_playwright() as p:
        context = siteaccounts.launch_profile(p, "strata", visible=False)
        page = siteaccounts.open_tab(context, SIGN_IN_URL)
        try:
            for i, b in enumerate(buildings):
                if cancel and cancel():
                    stopped = "Stopped"
                    break
                if i:
                    _pause(PAUSE_BETWEEN_BUILDINGS, cancel)
                if progress:
                    progress(i, len(buildings), b.split("/")[-1].replace("-", " "))
                try:
                    found = read_building(page, b, cancel)
                except StopRun as e:
                    stopped = str(e)
                    if "signed out" in stopped:
                        siteaccounts.mark_signed_out("strata")
                    else:
                        set_state(STATE_PAUSED, (datetime.now() + PAUSE_AFTER_BLOCK).isoformat(timespec="seconds"))
                    logger.warning("Building sales stopped at %s: %s", b, stopped)
                    break
                save_building_sales(found)
                save_area_sales([_as_area_sale(s, cities.get(b)) for s in found])
                set_state(STATE_CHECKED + b, date.today().isoformat())
                done += 1
                sales += len(found)
        finally:
            siteaccounts.keep_fresh(context, [SIGN_IN_URL])
            context.close()
    if progress:
        progress(len(buildings), len(buildings), "")
    return {"buildings": done, "sales": sales, "stopped": stopped}


def _all_buildings(listings) -> set[str]:
    return {b for lst in listings if (b := building_of(lst.url))}


def _as_area_sale(s: dict, city: Optional[str]) -> dict:
    """The same sale for the purchase-price estimates, keyed as the listing pages' records are."""
    return {"sale_key": f"strata:{s['address']}:unit-{s['unit'].lower()}:{s['sold_date']}",
            "sold_date": s["sold_date"], "sold_price": s["sold_price"], "city": city,
            "address": s["address"], "type": None, "beds": s["beds"], "sqft": s["sqft"]}


# --- What the app shows ----------------------------------------------------------------------

def _building_for(listing, all_sales: Optional[list[dict]] = None) -> Optional[str]:
    """The building whose sales apply to this listing: its own Strata building, or for a listing
    from another site, the Strata building at the same street address."""
    b = building_of(listing.url)
    if b:
        return b
    from listingtracker.purchaseprice import building_key
    key = building_key(listing.address)
    if not key:
        return None
    if all_sales is None:
        from listingtracker.db import get_building_sales
        all_sales = get_building_sales()
    city = (listing.city or "").lower().replace(" ", "-")
    for s in all_sales:
        if building_key(s["address"]) == key and (not city or s["building"].startswith(city + "/")):
            return s["building"]
    return None


def summary_for(listing, snap, today: Optional[date] = None) -> Optional[dict]:
    """The building's recent sales and what they say about this listing's price, or None when
    the building hasn't been read (or isn't one)."""
    from listingtracker.db import get_building_sales, get_state
    today = today or date.today()
    building = _building_for(listing)
    if not building:
        return None
    checked = get_state(STATE_CHECKED + building)
    since = (today - timedelta(days=SHOWN_MONTHS * 30)).isoformat()
    sales = [s for s in get_building_sales(building) if s["sold_date"] >= since]
    if not checked and not sales:
        return None
    return {"building": building, "checked": checked, **describe(sales, snap, today)}


def _ppsf(s: dict) -> Optional[float]:
    return s["sold_price"] / s["sqft"] if s.get("sqft") else None


def describe(sales: list[dict], snap, today: date) -> dict:
    """Numbers for the sales list: medians over the last year, sale-to-ask, and the sales most
    like this unit (same bedrooms, size within 15%)."""
    year_ago = (today - timedelta(days=365)).isoformat()
    last_year = [s for s in sales if s["sold_date"] >= year_ago]
    basis = last_year if len(last_year) >= 3 else sales
    ppsf = [p for s in basis if (p := _ppsf(s))]
    to_ask = [s["sold_price"] / s["list_price"] for s in basis if s.get("list_price")]

    beds = snap.beds if snap else None
    sqft = (snap.sqft_max or snap.sqft_min) if snap else None
    alike = [s for s in sales if beds is not None and s.get("beds") == beds
             and (not sqft or not s.get("sqft") or abs(s["sqft"] - sqft) <= 0.15 * sqft)]

    out = {
        "sales": [{**{k: s[k] for k in ("unit", "sold_date", "sold_price", "list_price", "beds", "beds_plus",
                                       "baths", "sqft", "parking")},
                   "alike": s in alike} for s in sales],
        "count_year": len(last_year),
        "basis": "last 12 months" if basis is last_year else f"last {SHOWN_MONTHS // 12} years",
        "median_price": statistics.median(s["sold_price"] for s in basis) if basis else None,
        "median_ppsf": round(statistics.median(ppsf)) if ppsf else None,
        "sold_to_ask": round(statistics.mean(to_ask) * 100, 1) if to_ask else None,
        "alike_count": len(alike),
        "alike_median": statistics.median(s["sold_price"] for s in alike[:6]) if alike else None,
    }
    price = snap.list_price if snap else None
    if price and sqft and out["median_ppsf"]:
        out["listing_ppsf"] = round(price / sqft)
        out["vs_building_pct"] = round((price / sqft / out["median_ppsf"] - 1) * 100, 1)
    if price and out["alike_median"]:
        out["vs_alike_pct"] = round((price / out["alike_median"] - 1) * 100, 1)
    return out

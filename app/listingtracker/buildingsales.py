"""Past sales of other units in the same building, read from strata.ca with the user's own account.

Strata shows a building's full listing history, sold prices included, only to signed-in members.
The user signs in once in their own browser (see siteaccounts); the sign-in is kept and reused,
read-only, by the app's page reader (see reader.py), which reads a building the way its page does:
it loads the page, makes the same requests the page's scripts make to Strata's data service, in the
same order and form, with the member's sign-in, and reads the sales from the replies.

The account must not get flagged, so this reads like a person browsing, slowly, and stops early:
  * each building is read once and its sales kept: past sales don't change, so it isn't read again;
  * on it, the "Sold" history and a few more pages of it, as "Show more" clicks, seconds apart;
  * 30 to 60 seconds between buildings, and at most 25 buildings a run;
  * any sign of a block or a challenge ends the run and pauses reading for a day;
  * finding itself signed out ends the run too (the user signs in again).
"""

from __future__ import annotations

import logging
import random
import re
import statistics
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

from listingtracker import siteaccounts
from listingtracker.reader import Reader

logger = logging.getLogger(__name__)

SIGN_IN_URL = "https://strata.ca/"
SITE = "https://strata.ca"
API_URL = "https://api.slaterealty.ca/graphql"  # Strata's data service, which its pages call
FIRST_SOLD = 10  # the page asks for the 10 latest sales, then 8 more per "Show more"
MORE_SOLD = 8

MAX_BUILDINGS_PER_RUN = 25
PAUSE_BETWEEN_BUILDINGS = (30.0, 60.0)
PAUSE_BETWEEN_CLICKS = (3.0, 7.0)
MAX_SHOW_MORE = 4          # 8 more sales each: a few years of sales in most buildings
YEARS_BACK = 3             # stop paging once the sales shown are older than this
PAUSE_AFTER_BLOCK = timedelta(hours=24)
SHOWN_MONTHS = 36          # how far back the app shows a building's sales

STATE_CHECKED = "building_sales_checked:"   # + building -> ISO date its history was read in full
STATE_PAUSED = "building_sales_paused_until"
STATE_LATEST = "building_sales_latest:"     # + building -> ISO date its newest sales were last looked at
LATEST_EVERY = timedelta(days=7)            # how often a comparables check may look again
MAX_CATCH_UP = 6                            # buildings looked at per comparables check


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
                      limit: int = MAX_BUILDINGS_PER_RUN) -> list[str]:
    """Buildings whose history hasn't been read yet, favourites' first; at most `limit`. A building
    read in full is never read again: its past sales are already stored."""
    due: dict[str, tuple] = {}
    for lst in listings:
        b = building_of(lst.url)
        if not b or not _is_multi_unit(lst, snapshots.get(lst.id)) or checked.get(b):
            continue
        rank = (not lst.favorited, lst.id)
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


def listings_request(building: str, states: list[str], limit: int, offset: int, order: str,
                     direction: str, ownership: str = "BUY") -> dict:
    """The body of one of the building page's listing requests, exactly as the page builds it."""
    from listingtracker.strata_query import SEARCH_LISTINGS_QUERY
    return {
        "query": SEARCH_LISTINGS_QUERY,
        "variables": {"filter": {
            "limit": limit,
            "listingSort": {"listingOrderBy": order, "sortDirection": direction},
            "offset": offset,
            "ownershipPreference": ownership,
            "propertyListingStates": states,
            "propertyUids": [building.split("/")[-1]],
        }},
        "operationName": "SEARCH_LISTINGS_QUERY",
    }


_CHALLENGE = re.compile(r"just a moment|checking your browser|access denied|are you a robot|captcha|too many requests",
                        re.IGNORECASE)
_SIGNED_OUT = re.compile(r"auth|token|sign.?in|log.?in|permission|forbidden", re.IGNORECASE)


# --- Reading, with the user's own strata.ca sign-in (see siteaccounts) ------------------------

def _pause(span: tuple[float, float], cancel: Optional[Callable[[], bool]] = None) -> None:
    end = time.monotonic() + random.uniform(*span)
    while time.monotonic() < end:
        if cancel and cancel():
            return
        time.sleep(0.5)


def _ask(reader, body: dict, headers: dict) -> dict:
    """One of the page's requests to the data service; anything but a plain answer stops the run."""
    status, reply = reader.call(API_URL, body, page_origin=SITE, headers=headers)
    if status == 401:
        raise StopRun("signed out of strata.ca")
    if status in (403, 429) or status >= 500:
        raise StopRun(f"strata.ca's data service answered {status}")
    errors = (reply or {}).get("errors") or []
    if any(_SIGNED_OUT.search(str(e.get("message", ""))) for e in errors):
        raise StopRun("signed out of strata.ca")
    if errors:
        logger.warning("strata.ca's data service reported: %s", str(errors)[:300])
    return reply or {}


def read_building(reader, building: str, headers: dict, cancel=None,
                  known: Optional[set] = None) -> list[dict]:
    """Every sale the building's history shows within YEARS_BACK, newest first. Given the sales
    already `known` ({(unit, sold date)}), it stops at the first page that reaches one of them:
    everything newer has then been read."""
    page = reader.get(f"{SITE}/{building}")
    if page.status in (401, 403, 429) or page.status >= 500:
        raise StopRun(f"strata.ca answered {page.status}")
    title = re.search(r"<title[^>]*>(.*?)</title>", page.html[:20000], re.S | re.I)
    if title and _CHALLENGE.search(title.group(1)):
        raise StopRun(f"strata.ca showed a check page ({title.group(1).strip()[:60]})")
    if page.status == 404:
        return []  # not a building page (any more)

    # What the page asks for as it opens: the building's homes for sale and for rent
    _ask(reader, listings_request(building, ["ACTIVE", "SOLD_CONDITIONALLY"], 1000, 0, "PRICE", "ASC", "BUY"), headers)
    _ask(reader, listings_request(building, ["ACTIVE", "SOLD_CONDITIONALLY"], 1000, 0, "PRICE", "ASC", "RENT"), headers)
    _pause(PAUSE_BETWEEN_CLICKS, cancel)  # reading the page, then choosing the sold history

    found: dict[tuple, dict] = {}
    cutoff = (date.today() - timedelta(days=365 * YEARS_BACK)).isoformat()
    offset, limit = 0, FIRST_SOLD
    for _ in range(1 + MAX_SHOW_MORE):
        if cancel and cancel():
            break
        reply = _ask(reader, listings_request(building, ["SOLD"], limit, offset, "UNAVAILABLE_DATE", "DESC"), headers)
        result = ((reply.get("data") or {}).get("listingsNew") or {})
        items = result.get("listings") or []
        sold = [it.get("propertyListing") or {} for it in items]
        sold = [pl for pl in sold if pl.get("propertyListingState") == "SOLD"]
        if sold and not any(pl.get("soldPrice") for pl in sold):
            raise StopRun("signed out of strata.ca")  # sold prices are shown to members only
        page_sales = parse_sales(reply, building)
        for s in page_sales:
            found[(s["unit"], s["sold_date"])] = s
        if known and any((s["unit"], s["sold_date"]) in known for s in page_sales):
            break  # caught up with what's stored
        offset += limit
        limit = MORE_SOLD
        total = (result.get("pagingStats") or {}).get("totalCount") or 0
        if not items or offset >= total:
            break  # no "Show more" any more
        if found and min(s["sold_date"] for s in found.values()) < cutoff:
            break
        _pause(PAUSE_BETWEEN_CLICKS, cancel)
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

    return _read_buildings(buildings, cities, progress, cancel)


def _read_buildings(buildings: list[str], cities: dict, progress=None, cancel=None,
                    newest_only: bool = False) -> dict:
    """Read these buildings' sales one after another, a person's pace apart, and store them.
    With `newest_only`, a building already read in full is read only back to the sales stored."""
    from listingtracker.db import get_building_sales, get_state, save_area_sales, save_building_sales, set_state

    done = sales = 0
    stopped = None
    with siteaccounts.claim("strata"), Reader([SIGN_IN_URL]) as reader:
        token = reader.stored(SITE, "Slate-Authorization")
        if not token:
            siteaccounts.mark_signed_out("strata")
            return {"buildings": 0, "sales": 0, "stopped": "Sign in to strata.ca first"}
        # What the page's scripts send with every request: the site, this browser's visitor id
        # (kept from the sign-in) and the member's sign-in
        headers = {"Slate-Website": "strata", "Slate-PV": reader.stored(SITE, "Slate-PV") or str(uuid.uuid4()),
                   "Slate-Authorization": token}
        for i, b in enumerate(buildings):
            if cancel and cancel():
                stopped = "Stopped"
                break
            if i:
                _pause(PAUSE_BETWEEN_BUILDINGS, cancel)
            if progress:
                progress(i, len(buildings), b.split("/")[-1].replace("-", " "))
            full = not (newest_only and get_state(STATE_CHECKED + b))
            known = None if full else {(s["unit"], s["sold_date"]) for s in get_building_sales(b)}
            try:
                found = read_building(reader, b, headers, cancel, known=known)
            except StopRun as e:
                stopped = str(e)
                if "signed out" in stopped:
                    siteaccounts.mark_signed_out("strata")
                else:
                    set_state(STATE_PAUSED, (datetime.now() + PAUSE_AFTER_BLOCK).isoformat(timespec="seconds"))
                logger.warning("Building sales stopped at %s: %s", b, stopped)
                break
            except Exception as e:  # the connection dropped: try the rest another day
                stopped = f"Couldn't reach strata.ca ({type(e).__name__})"
                logger.warning("Building sales stopped at %s: %s", b, e)
                break
            new = [s for s in found if not known or (s["unit"], s["sold_date"]) not in known]
            save_building_sales(new)
            save_area_sales([_as_area_sale(s, cities.get(b)) for s in new])
            if cancel and cancel():
                stopped = "Stopped"  # cut short: what was read is kept, and the rest read next time
                break
            today = date.today().isoformat()
            set_state(STATE_LATEST + b, today)
            if full:
                set_state(STATE_CHECKED + b, today)
            done += 1
            sales += len(new)
            if newest_only:
                logger.info("Newest sales of %s: %d new", b, len(new))
    if progress:
        progress(len(buildings), len(buildings), "")
    return {"buildings": done, "sales": sales, "stopped": stopped}


# --- Newer sales, when a listing's comparables are checked --------------------------------------

def buildings_for_comparables(listing) -> list[str]:
    """The listing's own building, then its comparables' buildings (best match first): the ones
    whose recent sales say most about its price. Houses have none."""
    from listingtracker.db import get_building_sales, get_comparables_for_favorite, get_latest_snapshot, get_listing_by_id
    all_sales = get_building_sales()
    candidates = [listing] + [get_listing_by_id(c["listing_id"]) for c in get_comparables_for_favorite(listing.id)]
    out: list[str] = []
    for lst in candidates:
        if not lst or not _is_multi_unit(lst, get_latest_snapshot(lst.id)):
            continue
        b = _building_for(lst, all_sales)
        if b and b not in out:
            out.append(b)
    return out


def catch_up(listings, cancel: Optional[Callable[[], bool]] = None) -> dict:
    """After a comparables check: the sales since the last look, in these listings' buildings and
    their comparables' buildings. Each building is looked at most once a week, at most
    MAX_CATCH_UP of them, and only back to the sales already stored."""
    from listingtracker.db import get_state
    if needs_sign_in() or paused_until():
        return {"buildings": 0, "sales": 0, "stopped": "Not signed in to strata.ca, or paused"}
    wanted: list[str] = []
    cities: dict[str, str] = {}
    for lst in listings:
        for b in buildings_for_comparables(lst):
            if b not in wanted:
                wanted.append(b)
                cities[b] = lst.city
    week_ago = (date.today() - LATEST_EVERY).isoformat()
    due = [b for b in wanted if (get_state(STATE_LATEST + b) or "") <= week_ago][:MAX_CATCH_UP]
    if not due:
        return {"buildings": 0, "sales": 0, "stopped": None}
    try:
        return _read_buildings(due, cities, cancel=cancel, newest_only=True)
    except siteaccounts.Busy:
        return {"buildings": 0, "sales": 0, "stopped": "strata.ca is already being read"}


_catching_up = threading.Lock()


def catch_up_soon(listing_ids: Iterable[int]) -> None:
    """catch_up() in the background, so a comparables check never waits on strata.ca. One at a time."""
    ids = list(listing_ids)

    def run():
        if not _catching_up.acquire(blocking=False):
            return
        try:
            from listingtracker.db import get_listing_by_id
            result = catch_up([lst for i in ids if (lst := get_listing_by_id(i))])
            if result["buildings"] or result["stopped"]:
                logger.info("Building sales caught up: %s", result)
        except Exception as e:
            logger.warning("Catching up building sales failed: %s", e)
        finally:
            _catching_up.release()

    threading.Thread(target=run, name="building-sales-catch-up", daemon=True).start()


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

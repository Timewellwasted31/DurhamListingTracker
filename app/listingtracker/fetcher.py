"""Read listing and search pages with our own page reader (see reader.py)."""

from __future__ import annotations

import gzip
import logging
import random
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from listingtracker.paths import HTML_CACHE_DIR
from listingtracker.models import Listing, Snapshot, Status
from listingtracker.db import (
    add_snapshot,
    delete_listing,
    listings_with_mls,
    replaceable,
    site_rank,
    tracked_mls_numbers,
    get_latest_snapshot,
    get_consecutive_blocked_count,
    update_listing,
    save_listing_images,
    get_listing_images,
    save_price_history,
    save_past_sales,
    save_area_sales,
)
from listingtracker.adapters.base import SiteAdapter, clean_parsed
from listingtracker.adapters.strata import StrataAdapter
from listingtracker.adapters.condos import CondosAdapter
from listingtracker.adapters.realtor import RealtorAdapter
from listingtracker.adapters.housesigma import HouseSigmaAdapter
from listingtracker.adapters.zolo import ZoloAdapter
from listingtracker.adapters.wahi import WahiAdapter
from listingtracker.adapters.rew import RewAdapter
from listingtracker.adapters.condonow import CondoNowAdapter
from listingtracker.adapters.condominiums import CondominiumsAdapter
from listingtracker.diffing import diff_snapshots, sort_changes
from listingtracker.reader import Reader, allowed

logger = logging.getLogger(__name__)

# Region names that should be resolved to actual cities
_REGION_NAMES = {
    "durham-region", "durham region", "york-region", "york region",
    "peel-region", "peel region", "halton-region", "halton region",
    "niagara-region", "niagara region", "simcoe-county", "simcoe county",
    "waterloo-region", "waterloo region", "wellington-county",
}

# Known city slugs for URL-based resolution
_CITY_SLUGS = {
    "ajax", "bowmanville", "brock", "brooklin", "clarington", "courtice",
    "greenbank", "hampton", "newcastle", "orono", "oshawa", "pickering",
    "port-perry", "scugog", "uxbridge", "whitby",
    "aurora", "east-gwillimbury", "georgina", "king", "markham",
    "newmarket", "richmond-hill", "stouffville", "vaughan",
    "brampton", "caledon", "mississauga",
    "burlington", "halton-hills", "milton", "oakville",
    "toronto", "etobicoke", "north-york", "scarborough",
    "barrie", "bradford", "collingwood", "innisfil", "midland",
    "orillia", "wasaga-beach",
    "hamilton", "stoney-creek", "brantford",
    "cambridge", "kitchener", "waterloo",
    "guelph", "london", "kingston", "ottawa",
    "belleville", "cobourg", "cornwall", "lindsay",
    "peterborough", "port-hope",
}


def resolve_city(scraped_city: str, url: str) -> str:
    """Resolve region-level city names to actual cities using the URL."""
    if not scraped_city:
        return _city_from_url(url) or ""
    if scraped_city.strip().lower().replace(" ", "-") not in _REGION_NAMES:
        return scraped_city
    resolved = _city_from_url(url)
    return resolved if resolved else scraped_city


def _city_from_url(url: str) -> Optional[str]:
    """Extract a city name from a listing URL path."""
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return None
    for slug in sorted(_CITY_SLUGS, key=len, reverse=True):
        if f"/{slug}/" in path or path.endswith(f"/{slug}"):
            return slug.replace("-", " ").title()
    return None

ADAPTERS: list[type[SiteAdapter]] = [
    StrataAdapter,
    CondosAdapter,
    RealtorAdapter,
    HouseSigmaAdapter,
    ZoloAdapter,
    WahiAdapter,
    RewAdapter,
    CondoNowAdapter,
    CondominiumsAdapter,
]

DOMAIN_LOAD_COUNTS: dict[str, int] = {}
_DOMAIN_LOCK = threading.Lock()
MAX_LOADS_PER_DOMAIN = 200


def get_adapter_for_url(url: str) -> Optional[SiteAdapter]:
    for cls in ADAPTERS:
        if cls.matches(url):
            return cls()
    return None


def _check_robots(url: str, reader: Optional[Reader] = None) -> bool:
    return allowed(url, reader)


def _random_delay(listing_index: int) -> None:
    base = random.uniform(2, 5)
    if listing_index > 0 and listing_index % 5 == 0:
        base += random.uniform(3, 8)
    time.sleep(base)


def _store_html(html: str, snapshot_id: int) -> str:
    today_dir = HTML_CACHE_DIR / date.today().isoformat()
    today_dir.mkdir(parents=True, exist_ok=True)
    path = today_dir / f"{snapshot_id}.html.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(html)
    return str(path)


def _get_domain(url: str) -> str:
    return urlparse(url).netloc


def _signed_in_domain(url: str) -> bool:
    from listingtracker import siteaccounts
    site = siteaccounts.site_for_url(url)
    return bool(site and siteaccounts.is_signed_in(site.key))


def _check_domain_limit(url: str) -> bool:
    domain = _get_domain(url)
    with _DOMAIN_LOCK:
        count = DOMAIN_LOAD_COUNTS.get(domain, 0)
    return count < MAX_LOADS_PER_DOMAIN


def _increment_domain_count(url: str) -> None:
    domain = _get_domain(url)
    with _DOMAIN_LOCK:
        DOMAIN_LOAD_COUNTS[domain] = DOMAIN_LOAD_COUNTS.get(domain, 0) + 1


def fetch_listing(
    listing: Listing,
    reader: Reader,
    adapter: SiteAdapter,
    listing_index: int = 0,
    progress_callback=None,
) -> tuple[Optional[Snapshot], list]:
    """Fetch a single listing. Returns (snapshot, changes)."""
    changes = []

    if not _check_domain_limit(listing.url):
        logger.warning("Domain load limit reached for %s, skipping", listing.url)
        return None, []

    if not _check_robots(listing.url, reader):
        logger.warning("Blocked by robots.txt: %s", listing.url)
        return None, []

    if listing_index > 0:
        _random_delay(listing_index)

    try:
        logger.info("Fetching %s", listing.url)

        page = reader.get(listing.url)
        html = page.html
        _increment_domain_count(listing.url)
        time.sleep(random.uniform(2, 6))  # time a person spends on the page: the pace sites saw before

        landed = urlparse(page.url or "").netloc
        if landed and not adapter.matches(landed):
            # The site sent us elsewhere (wahi.com has redirected to another brokerage since
            # Nov 13, 2026): that page is not this listing, so keep what's stored.
            logger.warning("%s now opens %s; keeping the stored data", listing.url, landed)
            return None, []

        if page.refused or adapter.detect_blocked(html):
            logger.warning("BLOCKED on %s (answered %d)", listing.url, page.status)
            prev = get_latest_snapshot(listing.id)

            # If we already have good data, don't overwrite with a blocked snapshot
            if prev and prev.list_price and prev.status not in (Status.BLOCKED, Status.UNKNOWN):
                logger.info("Keeping existing good data for %s (blocked this time)", listing.url)
                return None, []

            snap = Snapshot(
                listing_id=listing.id,
                fetched_at=datetime.utcnow().isoformat(),
                status=Status.BLOCKED,
                parse_errors=f"Blocked by {listing.site}",
            )
            snap_id = add_snapshot(snap)
            snap.id = snap_id

            blocked_count = get_consecutive_blocked_count(listing.id)
            if blocked_count >= 3:
                listing.active = False
                update_listing(listing)
                logger.warning("Auto-disabled %s after 3 consecutive blocks", listing.url)

            changes = diff_snapshots(listing, prev, snap)
            return snap, changes

        parsed = clean_parsed(adapter.parse(html, url=listing.url))

        if parsed.get("is_rental"):
            logger.info("Skipping rental listing: %s", listing.url)
            delete_listing(listing.id)
            return None, []

        # A home also on other sites: one copy is kept, so the same unit doesn't appear once per
        # site. A better site's copy (SITE_PREFERENCE) wins over one just found; a favourite, a
        # listing with notes or one tracked from before always stays.
        if parsed.get("mls_number") and get_latest_snapshot(listing.id) is None:
            others = listings_with_mls(parsed["mls_number"], other_than=listing.id)
            if any(site_rank(o["site"]) <= site_rank(listing.site) for o in others):
                logger.info("%s is MLS %s, already tracked from %s; not added twice", listing.url,
                            parsed["mls_number"], min(others, key=lambda o: site_rank(o["site"]))["site"])
                delete_listing(listing.id)
                return None, []
            for o in others:
                if replaceable(o):
                    logger.info("%s is MLS %s; it replaces the copy just found on %s",
                                listing.url, parsed["mls_number"], o["site"])
                    delete_listing(o["id"])

        if parsed.get("address"):
            listing.address = parsed["address"]
        if parsed.get("unit"):
            listing.unit = parsed["unit"]
        if parsed.get("building_name"):
            listing.building_name = parsed["building_name"]
        if parsed.get("city"):
            listing.city = resolve_city(parsed["city"], listing.url)
        elif not listing.city:
            resolved = _city_from_url(listing.url)
            if resolved:
                listing.city = resolved
        if parsed.get("neighbourhood"):
            listing.neighbourhood = parsed["neighbourhood"]
        if parsed.get("mls_number"):
            listing.mls_number = parsed["mls_number"]
        update_listing(listing)

        prev = get_latest_snapshot(listing.id)

        snap = Snapshot(
            listing_id=listing.id,
            fetched_at=datetime.utcnow().isoformat(),
            status=Status(parsed.get("status", "UNKNOWN")),
            list_price=parsed.get("list_price"),
            maintenance_fee=parsed.get("maintenance_fee"),
            taxes_annual=parsed.get("taxes_annual"),
            beds=parsed.get("beds"),
            baths=parsed.get("baths"),
            sqft_min=parsed.get("sqft_min"),
            sqft_max=parsed.get("sqft_max"),
            parking=parsed.get("parking"),
            locker=parsed.get("locker"),
            balcony=parsed.get("balcony"),
            exposure=parsed.get("exposure"),
            days_on_market=parsed.get("days_on_market"),
            site_estimate=parsed.get("site_estimate"),
            broker=parsed.get("broker"),
            fee_includes=parsed.get("fee_includes"),
            property_type=parsed.get("property_type"),
            year_built=parsed.get("year_built"),
            ensuite_laundry=parsed.get("ensuite_laundry"),
            outdoor_space=parsed.get("outdoor_space"),
            possession_date=parsed.get("possession_date"),
            rent_controlled=parsed.get("rent_controlled"),
            power_of_sale=parsed.get("power_of_sale"),
            parse_errors=parsed.get("parse_errors"),
        )

        snap_id = add_snapshot(snap)
        snap.id = snap_id
        save_price_history(listing.id, parsed.get("list_date"), parsed.get("price_history") or [])
        if "past_sales" in parsed:  # only sites that report sales; others leave what's stored
            save_past_sales(listing.id, parsed["past_sales"])
        save_area_sales(parsed.get("area_sales") or [])

        raw_path = _store_html(html, snap_id)
        snap.raw_html_path = raw_path

        if not get_listing_images(listing.id):
            try:
                image_urls = adapter.extract_images(html, url=listing.url)
                if image_urls:
                    save_listing_images(listing.id, image_urls, source=adapter.site_name)
            except Exception as e:
                logger.debug("Image extraction failed for %s: %s", listing.url, e)

        changes = diff_snapshots(listing, prev, snap)

        if parsed.get("parse_errors"):
            logger.warning("Parse errors for %s: %s", listing.url, parsed["parse_errors"])

        return snap, changes

    except Exception as e:
        logger.error("Error fetching %s: %s", listing.url, e, exc_info=True)
        try:
            snap = Snapshot(
                listing_id=listing.id,
                fetched_at=datetime.utcnow().isoformat(),
                status=Status.UNKNOWN,
                parse_errors=str(e),
            )
            snap_id = add_snapshot(snap)
            snap.id = snap_id
            return snap, []
        except Exception:
            return None, []


def _fetch_domain_batch(
    domain_listings: list[Listing],
    progress_callback,
    progress_lock,
    done_count: list,
    total: int,
    cancel_check=None,
) -> tuple[list[Snapshot], list]:
    """Fetch these listings from one site, one after another, as one visit."""
    snapshots: list[Snapshot] = []
    changes: list = []

    urls = [l.url for l in domain_listings]
    try:
        with Reader(urls) as reader:
            for i, listing in enumerate(domain_listings):
                if cancel_check and cancel_check():
                    break

                adapter = get_adapter_for_url(listing.url)
                if adapter is None:
                    logger.warning("No adapter for %s", listing.url)
                    if progress_callback and progress_lock:
                        with progress_lock:
                            done_count[0] += 1
                            progress_callback(
                                done_count[0] - 1, total,
                                listing.address or listing.url,
                            )
                    continue

                if progress_callback and progress_lock:
                    with progress_lock:
                        done_count[0] += 1
                        progress_callback(
                            done_count[0] - 1, total,
                            listing.address or listing.url,
                        )

                snap, ch = fetch_listing(listing, reader, adapter, listing_index=i)
                if snap:
                    snapshots.append(snap)
                changes.extend(ch)
    except Exception as e:
        logger.error("Domain batch fetch failed: %s", e, exc_info=True)

    return snapshots, changes


def _filter_already_fetched_today(
    listings: list[Listing],
) -> list[Listing]:
    """Remove listings that already have a snapshot from today."""
    from listingtracker.db import get_all_latest_snapshots
    latest = get_all_latest_snapshots()
    today = date.today().isoformat()
    need_fetch = []
    for lst in listings:
        snap = latest.get(lst.id)
        if snap and snap.fetched_at and snap.fetched_at[:10] == today:
            continue
        need_fetch.append(lst)
    return need_fetch


MAX_WORKERS = 8
BATCH_SIZE = 50


def fetch_all(
    listings: list[Listing],
    progress_callback=None,
    cancel_check=None,
    batch_callback=None,
) -> tuple[list[Snapshot], list]:
    """Fetch listings in batches, running domains in parallel within each batch.

    *batch_callback(snapshots, changes)* is called after each batch so the
    caller can refresh the UI mid-run.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    active = [l for l in listings if l.active]
    before_count = len(active)
    active = _filter_already_fetched_today(active)
    skipped = before_count - len(active)
    if skipped:
        logger.info("Skipped %d listings already fetched today", skipped)
    if not active:
        if progress_callback:
            progress_callback(0, 0, "All listings already up to date today")
        return [], []

    total = len(active)
    all_snapshots: list[Snapshot] = []
    all_changes: list = []

    for batch_start in range(0, len(active), BATCH_SIZE):
        if cancel_check and cancel_check():
            break

        batch = active[batch_start:batch_start + BATCH_SIZE]
        logger.info("Batch %d–%d of %d", batch_start + 1,
                     min(batch_start + len(batch), len(active)), len(active))

        with _DOMAIN_LOCK:
            DOMAIN_LOAD_COUNTS.clear()

        by_domain: dict[str, list[Listing]] = {}
        for listing in batch:
            domain = _get_domain(listing.url)
            by_domain.setdefault(domain, []).append(listing)

        progress_lock = threading.Lock()
        done_count = [batch_start]

        chunks: list[tuple[str, list[Listing]]] = []
        for domain, domain_listings in by_domain.items():
            if _signed_in_domain(domain_listings[0].url):
                # One visit at a time on a member account, as a person would browse
                chunks.append((domain, domain_listings))
                continue
            batch_total = len(batch)
            workers_for_domain = max(1, round(len(domain_listings) / max(batch_total, 1) * MAX_WORKERS))
            chunk_size = max(1, len(domain_listings) // workers_for_domain)
            for i in range(0, len(domain_listings), chunk_size):
                chunks.append((domain, domain_listings[i:i + chunk_size]))

        max_workers = min(len(chunks), MAX_WORKERS)

        batch_snaps: list[Snapshot] = []
        batch_changes: list = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for domain, chunk in chunks:
                futures[executor.submit(
                    _fetch_domain_batch,
                    chunk,
                    progress_callback, progress_lock, done_count, total,
                    cancel_check,
                )] = domain

            for f in as_completed(futures):
                domain = futures[f]
                try:
                    snaps, changes = f.result()
                    batch_snaps.extend(snaps)
                    batch_changes.extend(changes)
                    logger.info("Domain %s chunk: fetched %d snapshots", domain, len(snaps))
                except Exception as e:
                    logger.error("Fetch worker for %s failed: %s", domain, e)

        all_snapshots.extend(batch_snaps)
        all_changes.extend(batch_changes)

        if batch_callback:
            try:
                batch_callback(batch_snaps, batch_changes)
            except Exception:
                pass

    return all_snapshots, sort_changes(all_changes)


SEARCH_PAGES = 3  # results pages read per Zolo or REW search, while each brings new listings


def _search_one_site(
    site_urls: list[dict],
    cancel_check,
    progress_callback,
    progress_lock,
    done_count: list,
    total: int,
    tracked_mls: frozenset = frozenset(),
) -> set[str]:
    """Read one site's search pages, one after another, as one visit."""
    from listingtracker.search import (
        extract_listing_urls_from_html, extract_building_urls_from_html, in_searched_town,
        listing_cards, next_page,
    )

    site_discovered: set[str] = set()
    buildings_discovered: set[str] = set()

    urls = [s["url"] for s in site_urls]
    try:
        with Reader(urls) as reader:
            # A member account goes at a person's pace: pages a few times further apart
            slow = 3 if reader.signed_in else 1

            for i, search in enumerate(site_urls):
                if cancel_check and cancel_check():
                    break

                url = search["url"]
                site = search.get("site", "")
                desc = search["description"]

                if progress_callback and progress_lock:
                    with progress_lock:
                        done_count[0] += 1
                        progress_callback(done_count[0] - 1, total, desc)

                if not _check_robots(url, reader):
                    logger.warning("Blocked by robots.txt: %s", url)
                    continue

                try:
                    if i > 0:
                        time.sleep(random.uniform(3, 8) * slow)

                    page_urls: set[str] = set()
                    page_buildings: set[str] = set()
                    seen: set[str] = set()
                    passed = 0
                    queue, pages_read = [url], 0
                    while queue and pages_read < SEARCH_PAGES:
                        if cancel_check and cancel_check():
                            break
                        page_url = queue.pop(0)
                        if pages_read:
                            time.sleep(random.uniform(3, 8) * slow)
                        logger.info("Searching %s", page_url)
                        page = reader.get(page_url)
                        pages_read += 1
                        if page.refused:
                            logger.warning("%s answered %d to %s", site, page.status, page_url)
                            break
                        found = set(extract_listing_urls_from_html(page.html, site))
                        fresh = found - seen
                        seen |= found
                        if site == "strata.ca":
                            page_buildings |= set(extract_building_urls_from_html(page.html, site))
                        cards = listing_cards(page.html, site)
                        for u in fresh:
                            card = cards.get(u, {})
                            over_budget = bool(card.get("price") and search.get("max_price")
                                               and card["price"] > search["max_price"])
                            if (over_budget or card.get("mls") in tracked_mls
                                    or not in_searched_town(u, site, search.get("city", ""))):
                                passed += 1
                            else:
                                page_urls.add(u)
                        time.sleep(random.uniform(2, 5))  # time a person spends looking at the results
                        if fresh:  # a page past the end shows the first again
                            nxt = next_page(url, site, pages_read + 1)
                            if nxt:
                                queue.append(nxt)
                    if passed:
                        logger.info("Passed over %d listings on %s (elsewhere, over budget or already tracked)",
                                    passed, desc)

                    logger.info("Found %d URLs + %d buildings on %s",
                                len(page_urls), len(page_buildings), desc)
                    site_discovered.update(page_urls)
                    buildings_discovered.update(page_buildings)

                except Exception as e:
                    logger.error("Error searching %s: %s", url, e)

            # Phase 2: read discovered building pages for all their units
            if buildings_discovered:
                # Skip buildings we already found units from
                existing_buildings = set()
                for url in site_discovered:
                    parts = url.rstrip("/").rsplit("/", 1)
                    if len(parts) == 2 and parts[0].count("/") >= 3:
                        existing_buildings.add(parts[0])
                new_buildings = buildings_discovered - existing_buildings

                if new_buildings:
                    logger.info("Reading %d building pages for units", len(new_buildings))
                    for bi, bp_url in enumerate(sorted(new_buildings)):
                        if cancel_check and cancel_check():
                            break
                        try:
                            if progress_callback and progress_lock:
                                with progress_lock:
                                    bname = bp_url.rsplit("/", 1)[-1]
                                    progress_callback(
                                        done_count[0], total,
                                        f"Building {bi+1}/{len(new_buildings)}: {bname}",
                                    )
                            time.sleep(random.uniform(3, 7) * slow)
                            page = reader.get(bp_url)
                            if page.refused:
                                logger.warning("strata.ca answered %d to %s", page.status, bp_url)
                                continue
                            found = set(extract_listing_urls_from_html(page.html, "strata.ca"))
                            new = found - site_discovered
                            if new:
                                logger.info("Building %s: %d new units", bp_url, len(new))
                            site_discovered.update(found)
                        except Exception as e:
                            logger.error("Error reading building %s: %s", bp_url, e)
    except Exception as e:
        logger.error("Site search failed: %s", e)

    return site_discovered


def search_and_discover(
    search_urls: list[dict],
    progress_callback=None,
    cancel_check=None,
) -> list[str]:
    """Discover listing URLs via sitemaps and the sites' search pages.

    Sitemap discovery runs in parallel with the search pages for speed and coverage. Sites with
    accessible sitemaps (condos.ca, wahi.com) use the sitemap path; others read search pages.

    Args:
        search_urls: list of {site, city, url, description} from build_search_urls
        progress_callback: called with (index, total, description)
        cancel_check: callable returning True to abort

    Returns list of discovered listing URLs (deduplicated).
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from listingtracker.sitemap import SITEMAP_CONFIGS, discover_from_sitemaps

    by_site: dict[str, list[dict]] = {}
    for s in search_urls:
        by_site.setdefault(s["site"], []).append(s)

    # Determine which sites use sitemaps vs search pages
    sitemap_sites = [s for s in by_site if s in SITEMAP_CONFIGS]
    page_sites = {s: urls for s, urls in by_site.items() if s not in SITEMAP_CONFIGS}

    # Collect cities from search URLs for sitemap filtering
    all_cities = list(dict.fromkeys(s["city"] for s in search_urls))

    discovered: set[str] = set()
    total = len(search_urls)
    progress_lock = threading.Lock()
    done_count = [0]

    # Run sitemap + search page discovery in parallel
    max_workers = min(len(page_sites) + 1, 4)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: dict = {}

        # Submit sitemap discovery as one task (handles all sitemap sites)
        if sitemap_sites:
            def _sitemap_task():
                def _sm_progress(desc):
                    if progress_callback and progress_lock:
                        with progress_lock:
                            progress_callback(done_count[0], total, desc)
                return discover_from_sitemaps(
                    sitemap_sites, all_cities,
                    progress_callback=_sm_progress,
                    cancel_check=cancel_check,
                )
            futures[executor.submit(_sitemap_task)] = "sitemap"

        # Submit search page reading per site
        try:
            tracked = frozenset(tracked_mls_numbers())
        except Exception:  # no database yet
            tracked = frozenset()
        for site, urls in page_sites.items():
            futures[executor.submit(
                _search_one_site,
                urls, cancel_check,
                progress_callback, progress_lock, done_count, total, tracked,
            )] = site

        for f in as_completed(futures):
            label = futures[f]
            try:
                result = f.result()
                if label == "sitemap":
                    for site, urls in result.items():
                        discovered.update(urls)
                        # Advance progress for the sitemap sites' search URLs
                        sitemap_url_count = sum(len(by_site.get(s, [])) for s in sitemap_sites)
                        if progress_callback and progress_lock:
                            with progress_lock:
                                done_count[0] += sitemap_url_count
                                progress_callback(
                                    done_count[0], total,
                                    f"Sitemaps: {sum(len(u) for u in result.values())} listings found",
                                )
                        logger.info("Sitemap discovery: %d URLs across %d sites",
                                    sum(len(u) for u in result.values()), len(result))
                else:
                    discovered.update(result)
                    logger.info("Site %s contributed %d URLs", label, len(result))
            except Exception as e:
                logger.error("Search worker for %s failed: %s", label, e)

    if cancel_check and cancel_check():
        return sorted(discovered)

    # Filter through each adapter's is_listing_url() to drop non-listing pages
    filtered = set()
    for url in discovered:
        adapter = get_adapter_for_url(url)
        if adapter and adapter.is_listing_url(url):
            filtered.add(url)
    if len(filtered) < len(discovered):
        logger.info("Filtered %d non-listing URLs from discovery results",
                     len(discovered) - len(filtered))

    return sorted(filtered)


ENRICH_CANDIDATES_PER_SITE = 2  # search-result links opened per site while looking for a match
ENRICH_TIMEOUT = 240  # seconds allowed for the whole site search


def enrich_listing(
    listing: Listing,
    current_snap: Optional[Snapshot],
    search_urls: list[dict],
    progress_callback=None,
) -> list[dict]:
    """Search other sites for this listing and return parsed data per site.

    Args:
        listing: the listing to enrich
        current_snap: its latest snapshot
        search_urls: from build_enrichment_search_urls()
        progress_callback: called with (description_string,)

    Returns list of {site, url, parsed: dict} for each site where data was found.
    """
    from listingtracker.crossref import is_same_property, rank_candidate_urls
    from listingtracker.search import extract_listing_urls_from_html

    results = []

    if not search_urls:
        return results

    def confirmed(parsed: dict) -> bool:
        """A page only counts when it has a price and is verifiably this property."""
        price = parsed.get("list_price")
        return bool(price and price > 10000 and is_same_property(listing, parsed))

    deadline = time.monotonic() + ENRICH_TIMEOUT
    urls = [s["url"] for s in search_urls]
    with Reader(urls) as reader:
        for i, search in enumerate(search_urls):
            if time.monotonic() > deadline:
                logger.warning("Enrichment stopped after %ds; %d sites not searched",
                               ENRICH_TIMEOUT, len(search_urls) - i)
                break
            url = search["url"]
            site = search["site"]
            desc = search.get("description", site)

            if progress_callback:
                progress_callback(f"Searching {desc}")

            if not _check_robots(url, reader):
                logger.warning("Blocked by robots.txt: %s", url)
                continue

            try:
                if i > 0:
                    time.sleep(random.uniform(5, 12))

                logger.info("Enrichment: searching %s", url)
                page = reader.get(url)
                html = page.html
                _increment_domain_count(url)
                time.sleep(random.uniform(3, 6))
                if page.refused:
                    logger.info("Enrichment: %s answered %d", site, page.status)
                    continue

                adapter = get_adapter_for_url(url)
                if not adapter:
                    continue

                # The search may have redirected straight to the listing's own page
                parsed = clean_parsed(adapter.parse(html, url=page.url))
                found = confirmed(parsed)
                if found:
                    url = page.url

                if not found:
                    # A results page: open the likeliest links until one is confirmed as this
                    # property. Taking the first link blindly matched unrelated listings.
                    candidates = rank_candidate_urls(extract_listing_urls_from_html(html, site), listing)
                    for listing_url in candidates[:ENRICH_CANDIDATES_PER_SITE]:
                        logger.info("Enrichment: opening listing %s", listing_url)
                        time.sleep(random.uniform(3, 8))
                        page = reader.get(listing_url)
                        _increment_domain_count(listing_url)
                        time.sleep(random.uniform(2, 5))

                        listing_adapter = get_adapter_for_url(listing_url)
                        if not listing_adapter or page.refused:
                            continue
                        parsed = clean_parsed(listing_adapter.parse(page.html, url=listing_url))
                        if confirmed(parsed):
                            found = True
                            url = listing_url
                            break
                        logger.info("Enrichment: %s is a different property (%s), skipped",
                                    listing_url, parsed.get("address") or "no address")

                if found:
                    logger.info("Enrichment: found data on %s (price=%s)", site, parsed.get("list_price"))
                    results.append({"site": site, "url": url, "parsed": parsed})
                else:
                    logger.info("Enrichment: no confirmed match for this listing on %s", site)

            except Exception as e:
                logger.error("Enrichment error for %s: %s", site, e)

    return results

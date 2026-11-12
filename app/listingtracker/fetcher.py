"""Browser orchestration — fetch listings via patchright."""

from __future__ import annotations

import gzip
import logging
import random
import threading
import time
import math
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from urllib.robotparser import RobotFileParser
from urllib.parse import urlparse

from listingtracker.paths import HTML_CACHE_DIR, BROWSER_PROFILE_DIR
from listingtracker.models import Listing, Snapshot, Status
from listingtracker.db import (
    add_snapshot,
    delete_listing,
    get_latest_snapshot,
    get_consecutive_blocked_count,
    update_listing,
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


def _check_robots(url: str) -> bool:
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch("*", url)
    except Exception:
        return True


def _random_delay(listing_index: int) -> None:
    base = random.uniform(2, 5)
    if listing_index > 0 and listing_index % 5 == 0:
        base += random.uniform(3, 8)
    time.sleep(base)


def _random_viewport() -> tuple[int, int]:
    w = 1440 + random.randint(-80, 80)
    h = 900 + random.randint(-60, 60)
    return w, h


def _bezier_move(page, x: int, y: int) -> None:
    """Move mouse along a bezier curve to (x, y)."""
    cx, cy = random.randint(100, 500), random.randint(100, 300)

    steps = random.randint(15, 30)
    cp1x = cx + (x - cx) * random.uniform(0.2, 0.4) + random.randint(-50, 50)
    cp1y = cy + (y - cy) * random.uniform(0.1, 0.3) + random.randint(-30, 30)
    cp2x = cx + (x - cx) * random.uniform(0.6, 0.8) + random.randint(-50, 50)
    cp2y = cy + (y - cy) * random.uniform(0.7, 0.9) + random.randint(-30, 30)

    for i in range(1, steps + 1):
        t = i / steps
        bx = (1-t)**3*cx + 3*(1-t)**2*t*cp1x + 3*(1-t)*t**2*cp2x + t**3*x
        by = (1-t)**3*cy + 3*(1-t)**2*t*cp1y + 3*(1-t)*t**2*cp2y + t**3*y
        page.mouse.move(bx, by)
        time.sleep(random.uniform(0.01, 0.03))


def _random_scroll(page) -> None:
    """Randomized scroll depth and dwell."""
    scroll_amount = random.randint(300, 1200)
    page.mouse.wheel(0, scroll_amount)
    time.sleep(random.uniform(2, 6))


def _store_html(html: str, snapshot_id: int) -> str:
    today_dir = HTML_CACHE_DIR / date.today().isoformat()
    today_dir.mkdir(parents=True, exist_ok=True)
    path = today_dir / f"{snapshot_id}.html.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(html)
    return str(path)


def _get_domain(url: str) -> str:
    return urlparse(url).netloc


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
    page,
    adapter: SiteAdapter,
    listing_index: int = 0,
    headless: bool = True,
    progress_callback=None,
) -> tuple[Optional[Snapshot], list]:
    """Fetch a single listing. Returns (snapshot, changes)."""
    changes = []

    if not _check_domain_limit(listing.url):
        logger.warning("Domain load limit reached for %s, skipping", listing.url)
        return None, []

    if not _check_robots(listing.url):
        logger.warning("Blocked by robots.txt: %s", listing.url)
        return None, []

    if listing_index > 0:
        _random_delay(listing_index)

    try:
        logger.info("Fetching %s", listing.url)

        page.goto(listing.url, wait_until="domcontentloaded", timeout=30_000)

        vw, vh = _random_viewport()
        page.set_viewport_size({"width": vw, "height": vh})

        _bezier_move(page, random.randint(200, 600), random.randint(200, 400))
        _random_scroll(page)

        html = page.content()
        _increment_domain_count(listing.url)

        if adapter.detect_blocked(html):
            logger.warning("BLOCKED on %s", listing.url)
            prev = get_latest_snapshot(listing.id)
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

        raw_path = _store_html(html, snap_id)
        snap.raw_html_path = raw_path

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


def _clean_browser_locks() -> None:
    """Remove stale lock files that prevent Chromium from starting."""
    lock_file = BROWSER_PROFILE_DIR / "SingletonLock"
    if lock_file.exists():
        try:
            lock_file.unlink()
            logger.info("Removed stale browser lock file")
        except OSError:
            pass
    cookie_lock = BROWSER_PROFILE_DIR / "SingletonCookie"
    if cookie_lock.exists():
        try:
            cookie_lock.unlink()
        except OSError:
            pass


def _fetch_domain_batch(
    domain_listings: list[Listing],
    show_browser: bool,
    progress_callback,
    progress_lock,
    done_count: list,
    total: int,
    cancel_check=None,
) -> tuple[list[Snapshot], list]:
    """Fetch all listings for one domain in a dedicated browser."""
    from patchright.sync_api import sync_playwright

    snapshots: list[Snapshot] = []
    changes: list = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=not show_browser,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            vw, vh = _random_viewport()
            context = browser.new_context(viewport={"width": vw, "height": vh})
            page = context.new_page()

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

                snap, ch = fetch_listing(
                    listing, page, adapter, listing_index=i,
                    headless=not show_browser,
                )
                if snap:
                    snapshots.append(snap)
                changes.extend(ch)

            page.close()
            context.close()
            browser.close()
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


def fetch_all(
    listings: list[Listing],
    headless: bool = True,
    progress_callback=None,
    show_browser: bool = False,
    cancel_check=None,
) -> tuple[list[Snapshot], list]:
    """Fetch all listings, running domains in parallel for speed."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with _DOMAIN_LOCK:
        DOMAIN_LOAD_COUNTS.clear()
    _clean_browser_locks()

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

    by_domain: dict[str, list[Listing]] = {}
    for listing in active:
        domain = _get_domain(listing.url)
        by_domain.setdefault(domain, []).append(listing)

    total = len(active)
    progress_lock = threading.Lock()
    done_count = [0]

    all_snapshots: list[Snapshot] = []
    all_changes: list = []

    chunks: list[tuple[str, list[Listing]]] = []
    for domain, domain_listings in by_domain.items():
        workers_for_domain = max(1, round(len(domain_listings) / total * MAX_WORKERS))
        chunk_size = max(1, len(domain_listings) // workers_for_domain)
        for i in range(0, len(domain_listings), chunk_size):
            chunks.append((domain, domain_listings[i:i + chunk_size]))

    max_workers = min(len(chunks), MAX_WORKERS)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for domain, chunk in chunks:
            futures[executor.submit(
                _fetch_domain_batch,
                chunk, show_browser,
                progress_callback, progress_lock, done_count, total,
                cancel_check,
            )] = domain

        for f in as_completed(futures):
            domain = futures[f]
            try:
                snaps, changes = f.result()
                all_snapshots.extend(snaps)
                all_changes.extend(changes)
                logger.info("Domain %s chunk: fetched %d snapshots", domain, len(snaps))
            except Exception as e:
                logger.error("Fetch worker for %s failed: %s", domain, e)

    return all_snapshots, sort_changes(all_changes)


def _search_one_site(
    site_urls: list[dict],
    show_browser: bool,
    cancel_check,
    progress_callback,
    progress_lock,
    done_count: list,
    total: int,
) -> set[str]:
    """Search all pages for one site in its own browser instance."""
    from patchright.sync_api import sync_playwright
    from listingtracker.search import extract_listing_urls_from_html, extract_building_urls_from_html

    site_discovered: set[str] = set()
    buildings_discovered: set[str] = set()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=not show_browser,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            vw, vh = _random_viewport()
            context = browser.new_context(
                viewport={"width": vw, "height": vh},
            )
            page = context.new_page()

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

                if not _check_robots(url):
                    logger.warning("Blocked by robots.txt: %s", url)
                    continue

                try:
                    if i > 0:
                        time.sleep(random.uniform(3, 8))

                    logger.info("Searching %s", url)
                    page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                    _bezier_move(page, random.randint(200, 600), random.randint(200, 400))
                    time.sleep(random.uniform(1, 3))

                    page_urls: set[str] = set()
                    page_buildings: set[str] = set()
                    prev_count = 0
                    stale_scrolls = 0
                    for scroll_pass in range(20):
                        if cancel_check and cancel_check():
                            break
                        html = page.content()
                        found = extract_listing_urls_from_html(html, site)
                        page_urls.update(found)

                        # For strata.ca, also extract building URLs
                        if site == "strata.ca":
                            page_buildings.update(extract_building_urls_from_html(html, site))

                        combined = len(page_urls) + len(page_buildings)
                        if combined == prev_count:
                            stale_scrolls += 1
                            if stale_scrolls >= 3 and scroll_pass > 3:
                                break
                        else:
                            stale_scrolls = 0
                        prev_count = combined

                        page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                        time.sleep(random.uniform(1.5, 3))

                    logger.info("Found %d URLs + %d buildings on %s",
                                len(page_urls), len(page_buildings), desc)
                    site_discovered.update(page_urls)
                    buildings_discovered.update(page_buildings)

                except Exception as e:
                    logger.error("Error searching %s: %s", url, e)

            # Phase 2: scrape discovered building pages for all units
            if buildings_discovered:
                # Remove buildings we already found units from
                existing_buildings = set()
                for url in site_discovered:
                    parts = url.rstrip("/").rsplit("/", 1)
                    if len(parts) == 2 and parts[0].count("/") >= 3:
                        existing_buildings.add(parts[0])
                new_buildings = buildings_discovered - existing_buildings

                if new_buildings:
                    logger.info("Scraping %d building pages for units", len(new_buildings))
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
                            time.sleep(random.uniform(2, 5))
                            page.goto(bp_url, wait_until="domcontentloaded", timeout=30_000)
                            time.sleep(random.uniform(1, 2))
                            html = page.content()
                            found = extract_listing_urls_from_html(html, "strata.ca")
                            new = found - site_discovered
                            if new:
                                logger.info("Building %s: %d new units", bp_url, len(new))
                            site_discovered.update(found)
                        except Exception as e:
                            logger.error("Error scraping building %s: %s", bp_url, e)

            page.close()
            context.close()
            browser.close()
    except Exception as e:
        logger.error("Browser launch failed for site search: %s", e)

    return site_discovered


def search_and_discover(
    search_urls: list[dict],
    show_browser: bool = False,
    progress_callback=None,
    cancel_check=None,
) -> list[str]:
    """Discover listing URLs via sitemaps and browser-based search.

    Sitemap discovery runs in parallel with browser scraping for maximum
    speed and coverage. Sites with accessible sitemaps (condos.ca, wahi.com)
    use the sitemap path; others fall back to scroll-based browser search.

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

    # Determine which sites use sitemaps vs browser search
    sitemap_sites = [s for s in by_site if s in SITEMAP_CONFIGS]
    browser_sites = {s: urls for s, urls in by_site.items() if s not in SITEMAP_CONFIGS}

    # Collect cities from search URLs for sitemap filtering
    all_cities = list(dict.fromkeys(s["city"] for s in search_urls))

    discovered: set[str] = set()
    total = len(search_urls)
    progress_lock = threading.Lock()
    done_count = [0]

    _clean_browser_locks()

    # Run sitemap + browser discovery in parallel
    max_workers = min(len(browser_sites) + 1, 4)
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

        # Submit browser-based search per site
        for site, urls in browser_sites.items():
            futures[executor.submit(
                _search_one_site,
                urls, show_browser, cancel_check,
                progress_callback, progress_lock, done_count, total,
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
    show_browser: bool = False,
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
    from patchright.sync_api import sync_playwright
    from listingtracker.crossref import is_same_property, rank_candidate_urls
    from listingtracker.search import extract_listing_urls_from_html

    results = []

    if not search_urls:
        return results

    def confirmed(parsed: dict) -> bool:
        """A page only counts when it has a price and is verifiably this property."""
        price = parsed.get("list_price")
        return bool(price and price > 10000 and is_same_property(listing, parsed))

    _clean_browser_locks()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not show_browser,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        for i, search in enumerate(search_urls):
            url = search["url"]
            site = search["site"]
            desc = search.get("description", site)

            if progress_callback:
                progress_callback(f"Searching {desc}")

            if not _check_robots(url):
                logger.warning("Blocked by robots.txt: %s", url)
                continue

            try:
                if i > 0:
                    time.sleep(random.uniform(5, 12))

                logger.info("Enrichment: searching %s", url)
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                vw, vh = _random_viewport()
                page.set_viewport_size({"width": vw, "height": vh})

                _bezier_move(page, random.randint(200, 600), random.randint(200, 400))
                time.sleep(random.uniform(3, 6))
                _random_scroll(page)

                html = page.content()
                _increment_domain_count(url)

                adapter = get_adapter_for_url(url)
                if not adapter:
                    continue

                # The search may have redirected straight to the listing's own page
                parsed = clean_parsed(adapter.parse(html, url=url))
                found = confirmed(parsed)

                if not found:
                    # A results page: open the likeliest links until one is confirmed as this
                    # property. Taking the first link blindly matched unrelated listings.
                    candidates = rank_candidate_urls(extract_listing_urls_from_html(html, site), listing)
                    for listing_url in candidates[:ENRICH_CANDIDATES_PER_SITE]:
                        logger.info("Enrichment: navigating to listing %s", listing_url)
                        time.sleep(random.uniform(3, 8))
                        page.goto(listing_url, wait_until="domcontentloaded", timeout=30_000)
                        _bezier_move(page, random.randint(200, 600), random.randint(200, 400))
                        time.sleep(random.uniform(2, 5))
                        _random_scroll(page)
                        html = page.content()
                        _increment_domain_count(listing_url)

                        listing_adapter = get_adapter_for_url(listing_url)
                        if not listing_adapter:
                            continue
                        parsed = clean_parsed(listing_adapter.parse(html, url=listing_url))
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

        page.close()
        context.close()
        browser.close()

    return results


def _enrich_subprocess_main() -> None:
    """Entry point for the enrichment subprocess. Uses file-based I/O."""
    import json, sys, traceback

    from listingtracker.paths import LOGS_DIR, use_installed_browsers
    use_installed_browsers()

    try:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[logging.FileHandler(LOGS_DIR / f"tracker-{date.today().isoformat()}.log",
                                          encoding="utf-8")],
        )
    except OSError:
        pass

    input_path = None
    output_path = None
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--input" and i + 1 < len(args):
            input_path = args[i + 1]
        elif arg == "--output" and i + 1 < len(args):
            output_path = args[i + 1]

    if not input_path or not output_path:
        sys.exit(1)

    data = json.loads(Path(input_path).read_text(encoding="utf-8"))

    ld = data["listing"]
    if ld.get("date_added") and isinstance(ld["date_added"], str):
        try:
            ld["date_added"] = datetime.fromisoformat(ld["date_added"])
        except (ValueError, TypeError):
            ld["date_added"] = None
    listing = Listing(**ld)

    snap = None
    if data.get("snap"):
        sd = data["snap"]
        if sd.get("fetched_at") and isinstance(sd["fetched_at"], str):
            try:
                sd["fetched_at"] = datetime.fromisoformat(sd["fetched_at"])
            except (ValueError, TypeError):
                sd["fetched_at"] = None
        if sd.get("status") and isinstance(sd["status"], str):
            try:
                sd["status"] = Status(sd["status"])
            except (ValueError, KeyError):
                sd["status"] = Status.UNKNOWN
        snap = Snapshot(**sd)

    search_urls = data["search_urls"]
    show_browser = data.get("show_browser", False)

    try:
        results = enrich_listing(listing, snap, search_urls, show_browser=show_browser)
    except Exception:
        # The packaged app has no console, so the parent reads the reason from this file
        Path(output_path + ".err").write_text(traceback.format_exc(), encoding="utf-8")
        sys.exit(1)
    Path(output_path).write_text(json.dumps(results, default=str), encoding="utf-8")


def _enrich_command(input_file: Path, output_file: Path) -> list[str]:
    """Command that runs the site search in a separate copy of this app.

    The packaged app runs its own executable, so it needs nothing installed on the machine: no
    system Python, no packages, no source checkout.
    """
    import sys

    args = ["--enrich-subprocess", "--input", str(input_file), "--output", str(output_file)]
    if getattr(sys, "frozen", False):
        return [sys.executable] + args
    return [sys.executable, "-m", "listingtracker"] + args


def enrich_listing_safe(
    listing: Listing,
    current_snap: Optional[Snapshot],
    search_urls: list[dict],
    show_browser: bool = False,
    progress_callback=None,
) -> list[dict]:
    """Run enrich_listing in a subprocess so Chromium crashes don't kill the main app.

    Uses file-based I/O to fully isolate Chromium from the parent GUI process.
    """
    import json, subprocess, sys, os
    from dataclasses import asdict
    from listingtracker.paths import APP_DIR, CACHE_DIR

    listing_data = asdict(listing)
    snap_data = asdict(current_snap) if current_snap else None
    payload = json.dumps({
        "listing": listing_data,
        "snap": snap_data,
        "search_urls": search_urls,
        "show_browser": show_browser,
    }, default=str)

    env = os.environ.copy()

    if progress_callback:
        progress_callback("Starting enrichment subprocess...")

    tmp_dir = CACHE_DIR / "enrich"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    input_file = tmp_dir / "input.json"
    output_file = tmp_dir / "output.json"
    error_file = tmp_dir / "output.json.err"

    input_file.write_text(payload, encoding="utf-8")
    for stale in (output_file, error_file):
        stale.unlink(missing_ok=True)

    file_cmd = _enrich_command(input_file, output_file)

    logger.info("enrich_listing_safe: launching subprocess %s", file_cmd)
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    try:
        proc = subprocess.Popen(
            file_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            cwd=None if getattr(sys, "frozen", False) else str(APP_DIR),
            creationflags=creation_flags,
        )
        _, stderr = proc.communicate(timeout=ENRICH_TIMEOUT)
        returncode = proc.returncode
        logger.info("enrich_listing_safe: subprocess returned with exit code %d", returncode)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        logger.error("Enrichment subprocess timed out after %ds", ENRICH_TIMEOUT)
        return []
    except Exception as e:
        logger.error("enrich_listing_safe: Popen raised %s: %s", type(e).__name__, e)
        return []

    if returncode != 0:
        err_text = stderr.decode("utf-8", errors="replace") if stderr else ""
        if error_file.exists():
            err_text = error_file.read_text(encoding="utf-8", errors="replace")
            error_file.unlink(missing_ok=True)
        err_text = err_text[-1500:]  # the end of a traceback is the part that names the error
        logger.error("Enrichment subprocess failed (exit %d): %s", returncode, err_text)
        return []

    if not output_file.exists():
        logger.error("Enrichment subprocess produced no output file")
        return []

    try:
        return json.loads(output_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Enrichment subprocess output error: %s", e)
        return []
    finally:
        for f in (input_file, output_file):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass

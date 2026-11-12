"""Sitemap-based listing discovery.

Fetches XML sitemaps from condo-specific sites (condos.ca) and extracts
listing URLs filtered by city. Sites whose sitemaps contain all property
types (wahi.com, housesigma.com) use browser search instead.
"""

from __future__ import annotations

import logging
import re
import gzip
from io import BytesIO
from typing import Optional
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# Per-site sitemap configuration
SITEMAP_CONFIGS: dict[str, dict] = {
    "condos.ca": {
        "sitemaps": [
            "https://condos.ca/sitemap-listing-0.xml",
            "https://condos.ca/sitemap-listing-1.xml",
            "https://condos.ca/sitemap-listing-2.xml",
        ],
    },
    # wahi.com and housesigma.com sitemaps contain ALL property types
    # (houses, land, commercial) with no type indicator in URLs.
    # They use browser search instead, which limits to displayed results.
}

# City name variants that appear in URLs for Durham Region
DURHAM_CITY_SLUGS: dict[str, list[str]] = {
    "oshawa": ["oshawa"],
    "whitby": ["whitby"],
    "ajax": ["ajax"],
    "pickering": ["pickering"],
    "clarington": ["clarington", "bowmanville", "courtice", "newcastle"],
    "bowmanville": ["bowmanville"],
    "courtice": ["courtice"],
    "brooklin": ["brooklin"],
}


def _fetch_xml(url: str, timeout: int = 90) -> Optional[bytes]:
    """Fetch a sitemap URL and return raw bytes."""
    import urllib.request
    headers = {
        "User-Agent": "DurhamListingTracker/1.0 (sitemap reader)",
        "Accept": "application/xml, text/xml, */*",
        "Accept-Encoding": "gzip",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip" or url.endswith(".gz"):
                data = gzip.decompress(data)
            return data
    except Exception as e:
        logger.warning("Failed to fetch sitemap %s: %s", url, e)
        return None


def _parse_sitemap_urls(xml_bytes: bytes) -> list[str]:
    """Parse a sitemap XML and return all <loc> URLs.

    Uses iterparse for memory-efficient streaming of large sitemaps.
    Handles both sitemap index files and regular sitemaps.
    """
    urls = []
    try:
        loc_tag_sm = "{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
        for event, elem in ET.iterparse(BytesIO(xml_bytes), events=("end",)):
            if elem.tag == loc_tag_sm and elem.text:
                urls.append(elem.text.strip())
            elem.clear()
    except ET.ParseError as e:
        logger.warning("Failed to parse sitemap XML: %s", e)
    return urls


def _url_matches_city(url: str, city_slugs: set[str], site: str) -> bool:
    """Check if a listing URL is in one of the target cities."""
    path = urlparse(url).path.lower()

    if site == "condos.ca":
        # Pattern: /city/building/unit-...
        parts = path.strip("/").split("/")
        if parts:
            return parts[0] in city_slugs

    elif site == "wahi.com":
        # Pattern: /ca/en/real-estate/on/region/municipality/...
        for slug in city_slugs:
            if f"/{slug}/" in path:
                return True

    elif site == "housesigma.com":
        # Pattern: /on/{city}-real-estate/{address}/home/{id}/
        for slug in city_slugs:
            if f"/{slug}-real-estate/" in path:
                return True

    return False


def _fetch_and_parse_sub(url: str, city_slugs: set[str], site: str) -> set[str]:
    """Fetch one sub-sitemap and return matching city URLs."""
    data = _fetch_xml(url)
    if not data:
        return set()
    matched = set()
    for page_url in _parse_sitemap_urls(data):
        if page_url.endswith(".xml") or page_url.endswith(".xml.gz"):
            continue
        if _url_matches_city(page_url, city_slugs, site):
            matched.add(page_url)
    return matched


def discover_from_sitemaps(
    sites: list[str],
    cities: list[str],
    progress_callback=None,
    cancel_check=None,
) -> dict[str, set[str]]:
    """Discover listing URLs from XML sitemaps for supported sites.

    Sub-sitemaps are fetched in parallel (up to 6 concurrent) for speed.

    Args:
        sites: list of site names to check (e.g. ["condos.ca", "wahi.com"])
        cities: list of city names from the buyer profile
        progress_callback: called with (description_str,) for status updates
        cancel_check: callable returning True to abort

    Returns dict mapping site name to set of discovered listing URLs.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    city_slugs: set[str] = set()
    for city in cities:
        city_lower = city.lower().strip()
        city_slugs.add(city_lower)
        for slug in DURHAM_CITY_SLUGS.get(city_lower, []):
            city_slugs.add(slug)

    results: dict[str, set[str]] = {}

    for site in sites:
        if cancel_check and cancel_check():
            break

        config = SITEMAP_CONFIGS.get(site)
        if not config:
            continue

        if progress_callback:
            progress_callback(f"Fetching {site} sitemap...")

        site_urls: set[str] = set()
        all_fetch_urls: list[str] = []

        # Phase 1: fetch top-level sitemaps, collect sub-sitemap URLs
        for sitemap_url in config["sitemaps"]:
            if cancel_check and cancel_check():
                break

            logger.info("Fetching sitemap: %s", sitemap_url)
            data = _fetch_xml(sitemap_url)
            if data is None:
                continue

            parsed = _parse_sitemap_urls(data)
            sub_sitemaps = [u for u in parsed if u.endswith(".xml") or u.endswith(".xml.gz")]

            if sub_sitemaps and len(sub_sitemaps) == len(parsed):
                # Sitemap index — queue sub-sitemaps for parallel fetch
                all_fetch_urls.extend(sub_sitemaps)
                logger.info("Sitemap index %s has %d sub-sitemaps", sitemap_url, len(sub_sitemaps))
            else:
                # Direct listing URLs — filter now
                for url in parsed:
                    if not url.endswith(".xml") and _url_matches_city(url, city_slugs, site):
                        site_urls.add(url)

        # Phase 2: fetch sub-sitemaps in parallel
        if all_fetch_urls:
            if progress_callback:
                progress_callback(f"Fetching {len(all_fetch_urls)} {site} sub-sitemaps...")

            fetched = 0
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {
                    pool.submit(_fetch_and_parse_sub, url, city_slugs, site): url
                    for url in all_fetch_urls
                }
                for f in as_completed(futures):
                    if cancel_check and cancel_check():
                        pool.shutdown(wait=False, cancel_futures=True)
                        break
                    try:
                        site_urls.update(f.result())
                    except Exception as e:
                        logger.warning("Sub-sitemap fetch failed: %s", e)
                    fetched += 1
                    if progress_callback and fetched % 10 == 0:
                        progress_callback(
                            f"{site}: {fetched}/{len(all_fetch_urls)} sitemaps, "
                            f"{len(site_urls)} listings so far"
                        )

        if site_urls:
            logger.info("Sitemap discovery for %s: %d URLs in target cities", site, len(site_urls))
            results[site] = site_urls

    return results

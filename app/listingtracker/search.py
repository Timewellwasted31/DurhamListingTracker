"""Auto-search — build search URLs from profile criteria and discover listings."""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlencode, quote, urlparse

from listingtracker.profiles import BuyerProfile

logger = logging.getLogger(__name__)

ALL_SEARCH_SITES = [
    "strata.ca",
    "condos.ca",
    "realtor.ca",
    "zolo.ca",
    "housesigma.com",
    "wahi.com",
    "rew.ca",
    "condonow.com",
    "condominiums.ca",
]

CITY_SLUGS_STRATA = {
    "oshawa": "oshawa",
    "pickering": "pickering",
    "ajax": "ajax",
    "whitby": "whitby",
    "clarington": "clarington",
    "bowmanville": "clarington",
    "courtice": "clarington",
    "brooklin": "whitby",
}

CITY_SLUGS_REALTOR = {
    "oshawa": "oshawa",
    "pickering": "pickering",
    "ajax": "ajax",
    "whitby": "whitby",
    "clarington": "clarington",
    "bowmanville": "bowmanville",
    "courtice": "courtice",
}


def build_search_urls(profile: BuyerProfile, sites: list[str] | None = None) -> list[dict]:
    """Build search URLs for selected sites from profile criteria.

    Args:
        profile: buyer profile with search criteria
        sites: list of site names to include (None = all sites)

    Returns list of {site, city, url, description, max_price}.
    Uses the buffered price ceiling so we catch near-limit listings too.
    """
    enabled = set(sites) if sites else set(ALL_SEARCH_SITES)
    results = []
    price_max = int(profile.buffered_price_ceiling()) if profile.show_buffer_listings else int(profile.price_ceiling)
    price_min = int(profile.price_min) if profile.price_min > 0 else None

    prop_types = {t.lower() for t in profile.property_types} if profile.property_types else set()
    wants_condo = "condo" in prop_types or not prop_types
    wants_townhouse = "townhouse" in prop_types

    for city in profile.search_cities():
        city_lower = city.lower().strip()

        # strata.ca — separates condos and townhouses into different URL paths
        if "strata.ca" in enabled:
            strata_slug = CITY_SLUGS_STRATA.get(city_lower)
            if strata_slug:
                params = {}
                if price_min:
                    params["minPrice"] = price_min
                params["maxPrice"] = price_max
                if profile.min_beds > 0:
                    params["minBed"] = profile.min_beds
                if profile.min_sqft > 0:
                    params["minSqft"] = profile.min_sqft

                qs = ("?" + urlencode(params)) if params else ""

                if wants_condo:
                    results.append({
                        "site": "strata.ca",
                        "city": city,
                        "url": f"https://strata.ca/{strata_slug}/condos-for-sale{qs}",
                        "description": f"strata.ca — {city.title()} condos",
                    })

                if wants_townhouse:
                    results.append({
                        "site": "strata.ca",
                        "city": city,
                        "url": f"https://strata.ca/{strata_slug}/townhouses-for-sale{qs}",
                        "description": f"strata.ca — {city.title()} townhouses",
                    })

        # condos.ca — condo-focused site
        if "condos.ca" in enabled and wants_condo:
            results.append({
                "site": "condos.ca",
                "city": city,
                "url": f"https://condos.ca/{city_lower}/condos-for-sale"
                       f"?minPrice={price_min or ''}&maxPrice={price_max}"
                       f"&minBed={profile.min_beds}",
                "description": f"condos.ca — {city.title()} condos",
            })

        # realtor.ca, zolo.ca and rew.ca: their condo and townhouse pages (only REW takes a price
        # limit in the address; the others' results cards are checked against it instead)
        kinds = [k for k, wanted in (("condos", wants_condo), ("townhouses", wants_townhouse)) if wanted]
        if "realtor.ca" in enabled:
            realtor_slug = CITY_SLUGS_REALTOR.get(city_lower, city_lower)
            for kind in kinds:
                page = "condos-for-sale" if kind == "condos" else "townhomes-for-sale"
                results.append({
                    "site": "realtor.ca",
                    "city": city,
                    "url": f"https://www.realtor.ca/on/{realtor_slug}/{page}",
                    "description": f"realtor.ca — {city.title()} {kind}",
                })

        if "zolo.ca" in enabled:
            for kind in kinds:
                results.append({
                    "site": "zolo.ca",
                    "city": city,
                    "url": f"https://www.zolo.ca/{city_lower.replace(' ', '-')}-real-estate/{kind}",
                    "description": f"zolo.ca — {city.title()} {kind}",
                })

        # housesigma.com
        if "housesigma.com" in enabled:
            results.append({
                "site": "housesigma.com",
                "city": city,
                "url": f"https://housesigma.com/web/en/map?municipality={quote(city.title())}"
                       f"&price_min={price_min or 0}&price_max={price_max}"
                       f"&bed_min={profile.min_beds}",
                "description": f"housesigma.com — {city.title()} listings",
            })

        # wahi.com
        if "wahi.com" in enabled:
            results.append({
                "site": "wahi.com",
                "city": city,
                "url": f"https://wahi.com/ca/en/real-estate/on/gta/durham/{city_lower}",
                "description": f"wahi.com — {city.title()} listings",
            })

        if "rew.ca" in enabled:
            for kind in kinds:
                rew_type = "apartment-condo" if kind == "condos" else "townhouse"
                results.append({
                    "site": "rew.ca",
                    "city": city,
                    "url": f"https://www.rew.ca/properties/areas/{city_lower.replace(' ', '-')}-on/type/{rew_type}"
                           f"?list_price_to={price_max}",
                    "description": f"rew.ca — {city.title()} {kind}",
                })

        # condonow.com — condo-focused site
        if "condonow.com" in enabled and wants_condo:
            results.append({
                "site": "condonow.com",
                "city": city,
                "url": f"https://condonow.com/{city.title()}-Condos-for-Sale",
                "description": f"condonow.com — {city.title()} condos",
            })

        # condominiums.ca — condo-focused site
        if "condominiums.ca" in enabled and wants_condo:
            results.append({
                "site": "condominiums.ca",
                "city": city,
                "url": f"https://www.condominiums.ca/{city_lower}-condos-for-sale-and-rent",
                "description": f"condominiums.ca — {city.title()} condos",
            })

    for r in results:
        r["max_price"] = price_max
    return results


# --- Results pages -------------------------------------------------------------------------------

# Where each listing's card starts on a results page, for the sites whose cards are read
_CARD_START = {
    "zolo.ca": r'<article class="card-listing',
    "rew.ca": r"<article class='displaycard'",
    "realtor.ca": r'<a href="/real-estate/\d+/[^"]+" class="blockLink listingDetailsLink"',
}


def listing_cards(html: str, site: str) -> dict[str, dict]:
    """{listing url: {"price", "mls"}} from the cards on a Zolo, REW or Realtor.ca results page,
    so listings over budget or already tracked can be passed over without opening them."""
    from listingtracker.adapters.base import page_text
    pattern = _CARD_START.get(site)
    if not pattern:
        return {}
    starts = [m.start() for m in re.finditer(pattern, html)] + [len(html)]
    cards: dict[str, dict] = {}
    for a, b in zip(starts, starts[1:]):
        chunk = html[a:b]
        urls = extract_listing_urls_from_html(chunk, site)
        if not urls:
            continue
        # The card's own listing is its first link (the last card runs on into the page footer)
        url = min(urls, key=lambda u: chunk.find(urlparse(u).path))
        m = re.search(r'itemprop="price"[^>]*value="(\d+)"', chunk)
        text = page_text(re.sub(r"<script.*?</script>", " ", chunk, flags=re.S))
        price = float(m.group(1)) if m else None
        if price is None:
            pm = re.search(r"\$\s*([\d,]{5,})", text)
            price = float(pm.group(1).replace(",", "")) if pm else None
        mls = re.search(r"\b([A-Z]\d{7,8})\b", text)
        cards.setdefault(url, {"price": price, "mls": mls.group(1) if mls else None})
    return cards


def in_searched_town(url: str, site: str, city: str) -> bool:
    """Zolo, REW and Realtor.ca results pages also show listings elsewhere (featured or nearby,
    Calgary on an Oshawa page); their listing addresses name the town."""
    town = city.lower().strip().replace(" ", "-")
    path = url.lower().rstrip("/")
    if site == "zolo.ca":
        return f"/{town}-real-estate/" in path
    if site == "rew.ca":
        return path.endswith(f"-{town}-on")
    if site == "realtor.ca":
        return f"-{town}-" in path or path.endswith(f"-{town}")
    return True


def next_page(url: str, site: str, n: int) -> Optional[str]:
    """Page n of a Zolo or REW results page; None for sites that aren't paged this way."""
    base, _, query = url.partition("?")
    base = base.rstrip("/")
    if site == "zolo.ca":
        return f"{base}/page-{n}" + (f"?{query}" if query else "")
    if site == "rew.ca":
        return f"{base}/page/{n}" + (f"?{query}" if query else "")
    return None


def extract_building_urls_from_html(html: str, site: str) -> set[str]:
    """Extract unique building/complex page URLs from a search results page.

    For sites like strata.ca, listing URLs contain the building slug.
    Strip the unit suffix to get building pages, then scrape those for
    complete unit coverage.
    """
    buildings: set[str] = set()

    if site == "strata.ca":
        # Collect all strata.ca URLs from the page
        raw_urls: set[str] = set()
        for m in re.finditer(
            r'href="(https?://strata\.ca/[^"]+/[^"]*(?:unit|mls)[^"]*)"', html, re.IGNORECASE
        ):
            raw_urls.add(m.group(1))
        for m in re.finditer(
            r'"(https?://strata\.ca/[^"]+/(?:\d+-)?unit-[^"]*)"', html, re.IGNORECASE
        ):
            raw_urls.add(m.group(1))
        for m in re.finditer(
            r'href="(/[a-z][a-z0-9-]*/[a-z0-9][a-z0-9-]*/(?:\d+-)?unit-[^"]*)"', html
        ):
            raw_urls.add("https://strata.ca" + m.group(1))
        # Also pick up building-level links directly (2-segment paths: /city/building)
        for m in re.finditer(
            r'href="(/[a-z][a-z0-9-]*/[a-z0-9][a-z0-9-]*-condos(?:/[^"]*)?)"', html
        ):
            path = m.group(1).rstrip("/")
            if "/condos-for-sale" not in path and "/_next/" not in path:
                parts = path.strip("/").split("/")
                if len(parts) >= 2:
                    buildings.add(f"https://strata.ca/{parts[0]}/{parts[1]}")

        # Derive building pages from listing URLs by stripping the last segment
        for url in raw_urls:
            parts = url.rstrip("/").rsplit("/", 1)
            if len(parts) == 2 and parts[0].count("/") >= 3:
                buildings.add(parts[0])

    return buildings


def extract_listing_urls_from_html(html: str, site: str) -> list[str]:
    """Extract individual listing URLs from a search results page.

    Each site has different link patterns on their search/listing pages.
    """
    urls = set()

    if site == "strata.ca":
        # Absolute URLs in href attrs or quoted strings (RSC/script data)
        for m in re.finditer(
            r'href="(https?://strata\.ca/[^"]+/[^"]*\d+[^"]*)"', html
        ):
            url = m.group(1)
            if "/condos-for-sale" not in url and "/townhouses-for-sale" not in url:
                urls.add(url)
        for m in re.finditer(
            r'"(https?://strata\.ca/[^"]+/\d+[^"]*-\w+[^"]*)"', html
        ):
            url = m.group(1)
            if "/condos-for-sale" not in url and "/townhouses-for-sale" not in url:
                urls.add(url)
        # Relative URLs — Next.js renders <a href="/city/building/unit-slug">
        for m in re.finditer(
            r'href="(/[a-z][a-z0-9-]*/[a-z0-9][a-z0-9-]*/[^"]*\d+[^"]*)"', html
        ):
            path = m.group(1)
            if ("/condos-for-sale" not in path
                    and "/townhouses-for-sale" not in path
                    and not path.startswith("/_next/")):
                urls.add("https://strata.ca" + path)

    elif site == "condos.ca":
        for m in re.finditer(
            r'href="(https?://condos\.ca/[^"]+/[^"]*\d+[^"]*)"', html
        ):
            url = m.group(1)
            if "/condos-for-sale" not in url:
                urls.add(url)
        # Relative: /city/building/unit-MLS
        for m in re.finditer(
            r'href="(/[a-z][a-z0-9-]*/[a-z0-9][a-z0-9-]*/[^"]*\d+[^"]*)"', html
        ):
            path = m.group(1)
            if "/condos-for-sale" not in path and not path.startswith("/_"):
                urls.add("https://condos.ca" + path)

    elif site == "realtor.ca":
        for m in re.finditer(
            r'href="(https?://www\.realtor\.ca/real-estate/\d+/[^"]+)"', html
        ):
            urls.add(m.group(1))
        # Relative: /real-estate/12345678/address-slug
        for m in re.finditer(
            r'href="(/real-estate/\d+/[^"]+)"', html
        ):
            urls.add("https://www.realtor.ca" + m.group(1))
        # Script/JSON embedded URLs
        for m in re.finditer(
            r'"(https?://www\.realtor\.ca/real-estate/\d+/[^"]+)"', html
        ):
            urls.add(m.group(1))

    elif site == "housesigma.com":
        for m in re.finditer(
            r'href="(https?://housesigma\.com/[^"]*listing[/?][^"]+)"', html
        ):
            urls.add(m.group(1))
        # Script/JSON embedded listing URLs
        for m in re.finditer(
            r'"(https?://housesigma\.com/[^"]*listing/[^"]+)"', html
        ):
            urls.add(m.group(1))
        # Housesigma listing IDs in JSON data (id_listing=XXX or /listing/XXX)
        for m in re.finditer(
            r'"id_listing"\s*:\s*"([A-Za-z0-9]+)"', html
        ):
            urls.add(f"https://housesigma.com/web/en/listing/{m.group(1)}")

    elif site == "zolo.ca":
        # Listing URLs: /city-real-estate/address/unit
        for m in re.finditer(
            r'href="(https?://www\.zolo\.ca/[a-z]+-real-estate/[^"]+)"', html
        ):
            url = m.group(1)
            if "/condos-for-sale" not in url and "/houses-for-sale" not in url:
                urls.add(url)
        # Relative URLs
        for m in re.finditer(
            r'href="(/[a-z]+-real-estate/[^"]+)"', html
        ):
            path = m.group(1)
            if "/condos-for-sale" not in path and "/houses-for-sale" not in path:
                urls.add("https://www.zolo.ca" + path)

    elif site == "wahi.com":
        # Listing URLs have a street number in the last segment, e.g.
        # /ca/en/real-estate/on/gta/durham/whitby/86-solmar-ave-whitby-l1r3g2-ont
        # Category/neighbourhood pages are just single words like "townhouse"
        def _is_wahi_listing(u: str) -> bool:
            last = u.rstrip("/").rsplit("/", 1)[-1]
            return bool(re.search(r'\d', last))

        for m in re.finditer(
            r'href="(https?://wahi\.com/ca/en/real-estate/on/[^"]+)"', html
        ):
            url = m.group(1)
            if _is_wahi_listing(url):
                urls.add(url)
        for m in re.finditer(
            r'href="(/ca/en/real-estate/on/[^"]+)"', html
        ):
            path = m.group(1)
            if _is_wahi_listing(path):
                urls.add("https://wahi.com" + path)

    elif site == "rew.ca":
        # Listing URLs: /properties/address-city-province
        for m in re.finditer(
            r'href="(https?://www\.rew\.ca/properties/[^"]+)"', html
        ):
            url = m.group(1)
            if "/areas/" not in url:
                urls.add(url)
        for m in re.finditer(
            r'href="(/properties/[^"]+)"', html
        ):
            path = m.group(1)
            if "/areas/" not in path:
                urls.add("https://www.rew.ca" + path)

    elif site == "condonow.com":
        # Building pages: /BuildingName-Address (contains digits from street number)
        # Exclude search/category pages and static assets
        _condo_skip = {"-Condos-for-Sale", "-for-Sale", "/page/", "/wp-", "/assets/",
                       ".css", ".js", ".jpg", ".png", ".gif", "/feed"}
        for m in re.finditer(
            r'href="(https?://condonow\.com/[A-Z][^"]+)"', html
        ):
            url = m.group(1)
            if not any(s in url for s in _condo_skip) and re.search(r'\d', url):
                urls.add(url)

    elif site == "condominiums.ca":
        # Listing/building pages contain digits (street number) in the path
        # Exclude search pages, static assets, and overly-generic relative hrefs
        _condo_ca_skip = {"-condos-for-sale-and-rent", "/page/", "/wp-", "/assets/",
                          ".css", ".js", ".jpg", ".png", ".gif", "/feed", "/author/",
                          "/category/", "/tag/"}
        for m in re.finditer(
            r'href="(https?://www\.condominiums\.ca/[a-z0-9][^"]*\d[^"]*)"', html
        ):
            url = m.group(1)
            if not any(s in url for s in _condo_ca_skip):
                urls.add(url)

    return list(urls)


def classify_listing(
    price: Optional[float],
    monthly_allin: Optional[float],
    fee_psf: Optional[float],
    profile: BuyerProfile,
) -> str:
    """Classify a listing relative to profile limits.

    Returns: "within" | "buffer" | "over"
    """
    if price is not None and price > profile.buffered_price_ceiling():
        return "over"
    if monthly_allin is not None and monthly_allin > profile.buffered_monthly_ceiling():
        return "over"
    if fee_psf is not None and fee_psf > profile.buffered_fee_reject():
        return "over"

    in_buffer = False
    if price is not None and price > profile.price_ceiling:
        in_buffer = True
    if monthly_allin is not None and monthly_allin > profile.monthly_allin_ceiling:
        in_buffer = True
    if fee_psf is not None and fee_psf > profile.fee_per_sqft_reject:
        in_buffer = True

    return "buffer" if in_buffer else "within"


def check_deal_breakers(
    snapshot,
    profile: BuyerProfile,
) -> list[str]:
    """Check snapshot against profile deal-breakers. Returns list of triggered breakers."""
    triggered = []

    if profile.outdoor_space_required:
        if snapshot.balcony and snapshot.balcony.lower() in ("none", "no", "n/a", "—"):
            triggered.append("No outdoor space (balcony/patio/yard)")

    if profile.parking_required:
        if snapshot.parking and snapshot.parking.lower() in ("none", "no", "0", "n/a"):
            triggered.append("No parking included")

    if snapshot.list_price and snapshot.list_price > profile.price_ceiling:
        if snapshot.list_price > profile.buffered_price_ceiling():
            triggered.append(f"Over price ceiling + buffer (${snapshot.list_price:,.0f} > ${profile.buffered_price_ceiling():,.0f})")

    from listingtracker.finance import fee_per_sqft
    fpsf = fee_per_sqft(snapshot.maintenance_fee or 0, snapshot.sqft_min, snapshot.sqft_max)
    if fpsf is not None and fpsf > profile.fee_per_sqft_reject:
        triggered.append(f"Fee/sqft too high (${fpsf:.2f} > ${profile.fee_per_sqft_reject:.2f})")

    return triggered

"""Adapter for strata.ca listings.

Strata uses Next.js App Router with React Server Components (RSC).
Listing data lives in self.__next_f.push() script blocks, not in
conventional JSON-LD (which holds Strata's own brokerage info).
"""

from __future__ import annotations

import json
import re
import logging
from typing import Optional
from urllib.parse import urlparse

from listingtracker.adapters.base import SiteAdapter
from listingtracker.models import Status

logger = logging.getLogger(__name__)

# Strata's brokerage address — must be skipped when extracting listing address
_STRATA_OFFICE = "340 King St E"


class StrataAdapter(SiteAdapter):
    site_name = "strata.ca"
    domain_pattern = r"strata\.ca"

    @classmethod
    def is_listing_url(cls, url: str) -> bool:
        """strata.ca listing URLs contain /unit-, /N-unit-, or /mls/ after the building slug."""
        from urllib.parse import urlparse
        path = urlparse(url).path.rstrip("/")
        parts = path.strip("/").split("/")
        if len(parts) < 3:
            return False
        return bool(
            re.search(r'/(?:\d+-)?unit-', path, re.IGNORECASE)
            or '/mls/' in path.lower()
        )

    def parse(self, html: str, url: str = "") -> dict:
        errors = []
        result: dict = {}

        # Extract RSC payload — the primary data source
        rsc = self._extract_rsc_text(html)

        # Determine target unit from URL (e.g. /unit-801)
        target_unit = self._unit_from_url(url)

        # Try to find listing data in the RSC payload
        listing = self._find_listing_in_rsc(rsc, target_unit)

        if listing:
            result["address"] = listing.get("address", "")
            result["unit"] = listing.get("unit")
            result["mls_number"] = listing.get("mlsNumber")
            result["list_price"] = listing.get("listPrice")
            result["maintenance_fee"] = listing.get("maintenanceFees")
            result["taxes_annual"] = listing.get("taxesAnnual")
            result["beds"] = listing.get("bedroomCount")
            result["baths"] = listing.get("bathroomCount")
            result["sqft_min"] = listing.get("sqftSizeLow")
            result["sqft_max"] = listing.get("sqftSizeHigh")
            result["parking"] = (
                f"{listing['parkingSpaces']} space{'s' if listing['parkingSpaces'] != 1 else ''}"
                if listing.get("parkingSpaces") is not None
                else None
            )
            state = listing.get("propertyListingState", "")
            result["status"] = self._map_status(state)
            result["broker"] = listing.get("brokerage")
            result["possession_date"] = listing.get("possession")
            result["property_type"] = listing.get("propertyType")
            result["year_built"] = listing.get("yearBuilt")
            result["days_on_market"] = listing.get("daysOnMarket")
            result["exposure"] = listing.get("exposure")
            result["locker"] = listing.get("locker")
            if listing.get("ensuiteLaundry") is not None:
                result["ensuite_laundry"] = "Yes" if listing["ensuiteLaundry"] else "No"
            if listing.get("outdoorSpace"):
                result["outdoor_space"] = listing["outdoorSpace"]
            if listing.get("rentControlled") is not None:
                result["rent_controlled"] = "Yes" if listing["rentControlled"] else "No"
        else:
            logger.debug("RSC listing data not found; falling back to meta tags")
            result["list_price"] = self._price_from_dom(html)
            result["beds"] = self._beds_from_dom(html)
            result["baths"] = self._baths_from_dom(html)
            result["mls_number"] = self._mls_from_html(html)
            result["status"] = self._status_fallback(html, errors)

        # Address: prefer RSC, then JSON-LD building block, then og tags
        if not result.get("address"):
            result["address"] = self._address_from_jsonld(html) or self._address_from_og(html) or ""
        if not result.get("address"):
            errors.append("parse failed for strata.ca: address")

        # City: from JSON-LD building block, then URL, then og
        result.setdefault("city", self._city_from_jsonld(html) or self._city_from_url(url) or "")
        result.setdefault("neighbourhood",
                          self._neighbourhood_from_jsonld(html) or self._neighbourhood_from_dom(html))

        # Building name from JSON-LD or title
        result.setdefault("building_name", self._building_name(html))

        # Fee includes from JSON-LD amenityFeature
        result.setdefault("fee_includes", self._fee_includes(html))

        # DOM / JSON-LD fallbacks for fields not found via RSC
        if not result.get("unit"):
            result["unit"] = self._unit_fallback(html)
        if not result.get("maintenance_fee"):
            result["maintenance_fee"] = self._maint_fee_fallback(html)
        if not result.get("taxes_annual") or result.get("taxes_annual", 0) < 100:
            fb = self._taxes_fallback(html)
            if fb:
                result["taxes_annual"] = fb
        if result.get("sqft_min") is None:
            sq = self._sqft_fallback(html)
            result.setdefault("sqft_min", sq[0])
            result.setdefault("sqft_max", sq[1])
        if not result.get("parking"):
            result["parking"] = self._parking_fallback(html)
        if not result.get("locker"):
            result["locker"] = self._locker_fallback(html)
        if not result.get("exposure"):
            result["exposure"] = self._exposure_fallback(html)
        if result.get("days_on_market") is None:
            result["days_on_market"] = self._dom_fallback(html)
        if not result.get("broker"):
            result["broker"] = self._broker_fallback(html)

        # DOM fallbacks for new fields
        if not result.get("year_built"):
            result["year_built"] = self._year_built_fallback(html)
        if not result.get("ensuite_laundry"):
            result["ensuite_laundry"] = self._ensuite_laundry_fallback(html)
        if not result.get("outdoor_space"):
            result["outdoor_space"] = self._outdoor_space_fallback(html)
        if not result.get("possession_date"):
            result["possession_date"] = self._possession_fallback(html)
        if not result.get("rent_controlled"):
            result["rent_controlled"] = self._rent_controlled_fallback(html)
        if not result.get("property_type"):
            result["property_type"] = self._property_type_fallback(html, url)

        for key in ("unit", "locker", "exposure", "days_on_market", "broker",
                     "taxes_annual", "sqft_min", "sqft_max", "parking",
                     "balcony", "site_estimate", "property_type", "year_built",
                     "ensuite_laundry", "outdoor_space", "possession_date",
                     "rent_controlled"):
            result.setdefault(key, None)

        # Detect rental listings via HTML-level signals
        if self._is_rental_page(html):
            result["is_rental"] = True

        # Detect power of sale / distressed sale
        if self._detect_power_of_sale(html):
            result["power_of_sale"] = True

        result["parse_errors"] = "; ".join(errors) if errors else None
        return result

    @staticmethod
    def _is_rental_page(html: str) -> bool:
        head = html[:8000].lower()
        if re.search(r'for\s+rent', head):
            return True
        if re.search(r'\$[\d,]+\s*/\s*mth', head):
            return True
        if re.search(r'"propertyListingState"\s*:\s*"(?:FOR_LEASE|FOR_RENT)"', html, re.IGNORECASE):
            return True
        return False

    # --- RSC payload extraction ---

    @staticmethod
    def _extract_rsc_text(html: str) -> str:
        """Pull all self.__next_f.push() payloads into one searchable string."""
        chunks = re.findall(
            r'self\.__next_f\.push\(\s*\[\s*\d+\s*,\s*"((?:[^"\\]|\\.)*)"\s*\]',
            html,
        )
        if not chunks:
            return ""
        raw = "".join(chunks)
        # Un-escape the JS string escapes
        raw = raw.replace('\\"', '"').replace("\\'", "'")
        raw = raw.replace("\\n", "\n").replace("\\t", "\t")
        raw = raw.replace("\\\\", "\\")
        return raw

    @staticmethod
    def _unit_from_url(url: str) -> Optional[str]:
        # strata.ca URLs: /unit-622 or /3-unit-622 (floor prefix)
        m = re.search(r'(?:^|[/-])unit[- ]?(\d+\w*)', url, re.IGNORECASE)
        return m.group(1) if m else None

    def _find_listing_in_rsc(self, rsc: str, target_unit: Optional[str]) -> Optional[dict]:
        """Extract the best matching propertyListing from the RSC text."""
        if not rsc:
            return None

        # Find all listing-like objects by looking for listPrice + bedroomCount combos
        listings = []
        for m in re.finditer(r'"listPrice"\s*:\s*(\d+)', rsc):
            pos = m.start()
            # Extract a window around this listPrice to find related fields
            # Wide enough to capture all fields in the listing object
            window_start = max(0, pos - 1500)
            window_end = min(len(rsc), pos + 1500)
            window = rsc[window_start:window_end]

            listing = {}
            listing["listPrice"] = float(m.group(1))

            # Only accept reasonable prices (> $10,000)
            if listing["listPrice"] < 10000:
                continue

            # Extract fields from the window
            for key, pattern in [
                ("address", r'"address"\s*:\s*"([^"]+)"'),
                ("unit", r'"unit"\s*:\s*"(\d{1,5}\w?)"'),
                ("bedroomCount", r'"bedroomCount"\s*:\s*(\d+)'),
                ("bedroomPlusCount", r'"bedroomPlusCount"\s*:\s*(\d+)'),
                ("bathroomCount", r'"bathroomCount"\s*:\s*(\d+)'),
                ("sqftSizeLow", r'"sqftSizeLow"\s*:\s*(\d+)'),
                ("sqftSizeHigh", r'"sqftSizeHigh"\s*:\s*(\d+)'),
                ("maintenanceFees", r'"maintenanceFees"\s*:\s*([\d.]+)'),
                ("parkingSpaces", r'"parkingSpaces"\s*:\s*(\d+)'),
                ("mlsNumber", r'"mlsNumber"\s*:\s*"([^"]+)"'),
                ("propertyListingState", r'"propertyListingState"\s*:\s*"([^"]+)"'),
                ("possession", r'"possession"\s*:\s*"([^"]{1,40})"'),
                ("brokerage", r'"brokerage"\s*:\s*"([^"]+)"'),
                ("urlContext", r'"urlContext"\s*:\s*"([^"]+)"'),
                ("propertyType", r'"propertyType"\s*:\s*"([^"]+)"'),
                ("ensuiteLaundry", r'"ensuiteLaundry"\s*:\s*(true|false)'),
                ("outdoorSpace", r'"outdoorSpace"\s*:\s*"([^"]{1,50})"'),
                ("rentControlled", r'"rentControlled"\s*:\s*(true|false)'),
                ("exposure", r'"exposure"\s*:\s*"([^"]{1,30})"'),
                ("locker", r'"locker"\s*:\s*"([^"]{1,30})"'),
                ("taxesAnnual", r'"taxesAnnual"\s*:\s*([\d.]+)'),
                ("yearBuilt", r'"yearBuilt"\s*:\s*(\d{4})'),
                ("daysOnMarket", r'"daysOnMarket"\s*:\s*(\d+)'),
            ]:
                fm = re.search(pattern, window)
                if fm:
                    val = fm.group(1)
                    if key in ("bedroomCount", "bedroomPlusCount", "bathroomCount",
                               "sqftSizeLow", "sqftSizeHigh", "parkingSpaces",
                               "daysOnMarket"):
                        listing[key] = int(val)
                    elif key == "yearBuilt":
                        listing[key] = int(val)
                    elif key in ("maintenanceFees", "taxesAnnual"):
                        listing[key] = float(val)
                    elif key in ("ensuiteLaundry", "rentControlled"):
                        listing[key] = val.lower() == "true"
                    else:
                        listing[key] = val

            # Skip rental listings (active or past)
            state_lower = (listing.get("propertyListingState") or "").lower()
            if state_lower in ("leased", "for_lease", "for_rent", "rented"):
                continue

            # Validate maintenance fee — reject per-sqft values (< $50)
            mf = listing.get("maintenanceFees")
            if mf is not None and mf < 50:
                listing.pop("maintenanceFees", None)

            # Validate exposure — must look like compass directions
            exp = listing.get("exposure")
            if exp and not re.match(r'^[NSEW,/ ]+$', exp, re.IGNORECASE):
                valid_dirs = re.findall(r'\b(North|South|East|West|N|S|E|W|NE|NW|SE|SW)\b', exp, re.IGNORECASE)
                if valid_dirs:
                    listing["exposure"] = ", ".join(d.title() for d in valid_dirs)
                else:
                    listing.pop("exposure", None)

            # Validate locker — must be short, not description text
            lk = listing.get("locker")
            if lk and (len(lk) > 20 or re.search(r'(?:located|developed|building|floor)', lk, re.IGNORECASE)):
                listing.pop("locker", None)

            # Validate outdoor space — reject description fragments
            os_val = listing.get("outdoorSpace")
            if os_val and re.search(r'(?:located|developed|laundry|scenic|views)', os_val, re.IGNORECASE):
                listing.pop("outdoorSpace", None)

            listings.append(listing)

        if not listings:
            return None

        def _richness(lst):
            """Score by how many fields were extracted — richer = better data."""
            return sum(1 for k, v in lst.items() if v is not None and k != "listPrice")

        # If we have a target unit, find the richest match
        if target_unit:
            candidates = []
            for lst in listings:
                if lst.get("unit") == target_unit:
                    candidates.append(lst)
                elif f"unit-{target_unit}" in lst.get("urlContext", "").lower():
                    candidates.append(lst)
            if candidates:
                return max(candidates, key=_richness)

        # No target unit or not found — return the richest active listing
        active = [lst for lst in listings if lst.get("propertyListingState") == "ACTIVE"]
        if active:
            return max(active, key=_richness)
        return max(listings, key=_richness)

    @staticmethod
    def _map_status(state: str) -> str:
        mapping = {
            "ACTIVE": Status.FOR_SALE.value,
            "SOLD": Status.SOLD.value,
            "SOLD_CONDITIONALLY": Status.SOLD_CONDITIONAL.value,
            "TERMINATED": Status.TERMINATED.value,
            "EXPIRED": Status.EXPIRED.value,
            "SUSPENDED": Status.DELISTED.value,
            "LEASED": Status.DELISTED.value,
        }
        return mapping.get(state, Status.UNKNOWN.value)

    # --- Fallback extractors (DOM / meta tags) ---

    @staticmethod
    def _address_from_jsonld(html: str) -> Optional[str]:
        """Get address from JSON-LD LocalBusiness block (NOT the RealEstateAgent block)."""
        for m in re.finditer(r'"streetAddress"\s*:\s*"([^"]+)"', html):
            addr = m.group(1).strip()
            if _STRATA_OFFICE not in addr:
                return addr
        return None

    @staticmethod
    def _address_from_og(html: str) -> Optional[str]:
        m = re.search(r'<meta\s+property="og:street-address"\s+content="([^"]+)"', html)
        if m:
            addr = m.group(1).strip()
            if _STRATA_OFFICE not in addr:
                return addr
        return None

    @staticmethod
    def _city_from_jsonld(html: str) -> Optional[str]:
        """Get city from the LocalBusiness JSON-LD (skip the RealEstateAgent block)."""
        matches = re.findall(r'"addressLocality"\s*:\s*"([^"]+)"', html)
        for city in matches:
            if city.strip().lower() != "toronto":
                return city.strip()
        # If only Toronto found, check URL for Durham cities
        return None

    @staticmethod
    def _city_from_url(url: str) -> Optional[str]:
        parsed = urlparse(url)
        path = parsed.path.lower()
        for city in ("pickering", "ajax", "whitby", "oshawa", "clarington",
                     "bowmanville", "courtice", "newcastle"):
            if f"/{city}/" in path:
                return city.title()
        return None

    @staticmethod
    def _neighbourhood_from_jsonld(html: str) -> Optional[str]:
        m = re.search(
            r'"BreadcrumbList".*?"itemListElement".*?'
            r'"position"\s*:\s*2.*?"name"\s*:\s*"([^"]+)"',
            html, re.DOTALL,
        )
        return m.group(1).strip() if m else None

    @staticmethod
    def _neighbourhood_from_dom(html: str) -> Optional[str]:
        m = re.search(r'<[^>]*class="[^"]*neighbourhood[^"]*"[^>]*>([^<]+)<', html, re.IGNORECASE)
        return m.group(1).strip() if m else None

    @staticmethod
    def _building_name(html: str) -> Optional[str]:
        m = re.search(r'<[^>]*class="[^"]*building-name[^"]*"[^>]*>([^<]+)<', html)
        if m:
            return m.group(1).strip()
        m = re.search(r'"name"\s*:\s*"([^"]+(?:Condo|Tower|Residences|Place|Court|Terrace)[^"]*)"', html, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        m = re.search(r'<title>([^,|<]+)', html)
        if m:
            name = m.group(1).strip()
            if name and "Strata" not in name:
                return name
        return None

    @staticmethod
    def _fee_includes(html: str) -> Optional[str]:
        names = re.findall(
            r'"name"\s*:\s*"Maintenance fee includes\s+([^"\\]+)"',
            html, re.IGNORECASE,
        )
        if names:
            return ", ".join(names)
        m = re.search(r'(?:Fee\s+Includes?)\s*:?\s*([^<\n]{3,200})', html, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return None

    # --- DOM / HTML fallback extractors ---

    def _price_from_dom(self, html: str) -> Optional[float]:
        m = re.search(r'class="[^"]*price[^"]*"[^>]*>\s*\$?([\d,]+)', html, re.IGNORECASE)
        if m:
            price = self._safe_price(m.group(1))
            if price and price > 50000:
                return price
        for m in re.finditer(r'\$([\d,]{6,})', html):
            price = self._safe_price(m.group(1))
            if price and price > 50000:
                return price
        return None

    def _beds_from_dom(self, html: str) -> Optional[int]:
        m = re.search(r'"numberOfBedrooms"\s*:\s*"?(\d+)"?', html)
        if m:
            return self._safe_int(m.group(1))
        m = re.search(r'(\d+)\s*(?:\+\s*\d+\s*)?bed', html, re.IGNORECASE)
        return self._safe_int(m.group(1)) if m else None

    def _baths_from_dom(self, html: str) -> Optional[int]:
        m = re.search(r'"numberOfBathroomsTotal"\s*:\s*"?(\d+)"?', html)
        if m:
            return self._safe_int(m.group(1))
        m = re.search(r'(\d+)\s*bath', html, re.IGNORECASE)
        return self._safe_int(m.group(1)) if m else None

    def _mls_from_html(self, html: str) -> Optional[str]:
        m = re.search(r'MLS[®#:\s]*([A-Z]\d{7,})', html, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        m = re.search(r'"mlsNumber"\s*:\s*"([^"]+)"', html)
        return m.group(1).strip() if m else None

    def _status_fallback(self, html: str, errors: list) -> str:
        m = re.search(r'"propertyListingState"\s*:\s*"([^"]+)"', html)
        if m:
            return self._map_status(m.group(1))
        m = re.search(r'class="[^"]*status[^"]*"[^>]*>([^<]+)<', html, re.IGNORECASE)
        if m:
            return Status.normalize(m.group(1).strip()).value
        if re.search(r'\$[\d,]+', html):
            return Status.FOR_SALE.value
        errors.append("parse failed for strata.ca: status")
        return Status.UNKNOWN.value

    def _unit_fallback(self, html: str) -> Optional[str]:
        m = re.search(r'"unitNumber"\s*:\s*"([^"]+)"', html)
        if m:
            return m.group(1).strip()
        m = re.search(r'(?:Unit|Suite|Apt|#)\s*(\d+\w*)', html, re.IGNORECASE)
        return m.group(1).strip() if m else None

    def _maint_fee_fallback(self, html: str) -> Optional[float]:
        m = re.search(r'"maintenanceFee"\s*:\s*"?\$?([\d,.]+)"?', html)
        if m:
            return self._safe_float(m.group(1))
        m = re.search(r'(?:Maintenance|Maint\.?\s*Fee|Condo Fee)[^$]*\$([\d,]+(?:\.\d{2})?)', html, re.IGNORECASE)
        return self._safe_float(m.group(1)) if m else None

    def _taxes_fallback(self, html: str) -> Optional[float]:
        # Try the RSC payload first — most reliable
        m = re.search(r'"taxesAnnual"\s*:\s*([\d.]+)', html)
        if m:
            val = self._safe_float(m.group(1))
            if val and val >= 100:
                return val
        # HTML pattern from Highlights section: "Taxes" ... "$X,XXX"
        for fm in re.finditer(
            r'(?:Tax(?:es)?|Property Tax)[^$]{0,40}\$([\d,]+(?:\.\d{2})?)',
            html, re.IGNORECASE,
        ):
            val = self._safe_float(fm.group(1))
            if val and val >= 100:
                return val
        return None

    def _sqft_fallback(self, html: str) -> tuple[Optional[int], Optional[int]]:
        m = re.search(r'"floorSize"\s*:\s*\{[^}]*"value"\s*:\s*"?(\d[\d,]*)"?', html)
        if m:
            v = self._safe_int(m.group(1))
            return v, v
        range_m = re.search(r'(\d[\d,]*)\s*[-–]\s*(\d[\d,]*)\s*(?:sq\.?\s*ft|sqft|sf)', html, re.IGNORECASE)
        if range_m:
            return self._safe_int(range_m.group(1)), self._safe_int(range_m.group(2))
        m = re.search(r'(\d[\d,]*)\s*(?:sq\.?\s*ft|sqft|sf)', html, re.IGNORECASE)
        if m:
            v = self._safe_int(m.group(1))
            return v, v
        return None, None

    @staticmethod
    def _parking_fallback(html: str) -> Optional[str]:
        m = re.search(r'(?:Parking|Garage)\s*:?\s*([^<\n"\\]{1,80})', html, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if not re.search(r'[{\\]|propertyListingState', val):
                return val
        return None

    @staticmethod
    def _locker_fallback(html: str) -> Optional[str]:
        m = re.search(r'(?:Locker|Storage)\s*:?\s*(Yes|No|Owned|Ensuite|None|N/?A|Exclusive|Included)', html, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def _exposure_fallback(html: str) -> Optional[str]:
        m = re.search(r'(?:Exposure|Facing|Direction)\s*:?\s*([NSEW][^<\n]{0,20})', html, re.IGNORECASE)
        return m.group(1).strip() if m else None

    def _dom_fallback(self, html: str) -> Optional[int]:
        m = re.search(r'(\d+)\s*(?:days?\s*on\s*market|DOM)', html, re.IGNORECASE)
        return self._safe_int(m.group(1)) if m else None

    @staticmethod
    def _broker_fallback(html: str) -> Optional[str]:
        m = re.search(r'(?:Listed by|Listing Agent|Brokerage)\s*:?\s*([^<\n"\\]{3,80})', html, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if not re.search(r'[{\\]|"url"|"logo"', val):
                return val
        return None

    def _year_built_fallback(self, html: str) -> Optional[int]:
        m = re.search(r'"yearBuilt"\s*:\s*"?(\d{4})"?', html)
        if m:
            return self._safe_int(m.group(1))
        m = re.search(r'(?:Year\s*Built|Built\s*in|Completed\s*in)\s*:?\s*(\d{4})', html, re.IGNORECASE)
        if m:
            return self._safe_int(m.group(1))
        return None

    @staticmethod
    def _ensuite_laundry_fallback(html: str) -> Optional[str]:
        m = re.search(r'(?:Ensuite|In-Suite|In Suite)\s*Laundry\s*:?\s*(Yes|No)', html, re.IGNORECASE)
        if m:
            return m.group(1).title()
        if re.search(r'(?:Ensuite|In-Suite)\s*Laundry', html, re.IGNORECASE):
            return "Yes"
        return None

    @staticmethod
    def _outdoor_space_fallback(html: str) -> Optional[str]:
        m = re.search(r'(?:Outdoor\s*Space|Balcony|Terrace)\s*:?\s*([^<\n"]{1,40})', html, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val and not re.search(r'[{\\]', val):
                return val
        return None

    @staticmethod
    def _possession_fallback(html: str) -> Optional[str]:
        m = re.search(r'(?:Possession|Occupancy)\s*(?:Date)?\s*:?\s*([^<\n"]{1,40})', html, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val and not re.search(r'[{\\]', val):
                return val
        return None

    @staticmethod
    def _rent_controlled_fallback(html: str) -> Optional[str]:
        m = re.search(r'Rent\s*Controlled\s*:?\s*(Yes|No)', html, re.IGNORECASE)
        return m.group(1).title() if m else None

    @staticmethod
    def _property_type_fallback(html: str, url: str) -> Optional[str]:
        m = re.search(r'"propertyType"\s*:\s*"([^"]+)"', html)
        if m:
            return m.group(1).strip()
        url_lower = url.lower()
        if "townhouse" in url_lower or "townhome" in url_lower:
            return "Townhouse"
        if "condo" in url_lower:
            return "Condo Apartment"
        if "house" in url_lower or "detached" in url_lower:
            return "Detached"
        return None

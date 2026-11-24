"""Reader for realtor.ca listing pages.

The page gives the street address and "Town (Neighbourhood), Ontario" in its heading, the price
and MLS number under it, bedroom and bathroom counts as icons, and the rest as labelled details
(<div class="propertyDetailsSectionContentLabel">Annual Property Taxes</div> + its value).
Realtor.ca shows listings for sale or rent only: it has no sold prices or listing history.
"""

from __future__ import annotations

import re
import logging
from typing import Optional

from listingtracker.adapters.base import (
    SiteAdapter, money, page_text, property_kind, split_unit, sqft_range,
)
from listingtracker.models import Status

logger = logging.getLogger(__name__)


class RealtorAdapter(SiteAdapter):
    site_name = "realtor.ca"
    domain_pattern = r"realtor\.ca"

    @classmethod
    def is_listing_url(cls, url: str) -> bool:
        return bool(re.search(r"realtor\.ca/real-estate/\d+/", url))

    def parse(self, html: str, url: str = "") -> dict:
        errors = []
        result: dict = {}
        facts: dict[str, str] = {}
        for k, v in re.findall(r'class="propertyDetailsSectionContentLabel">(.*?)</div>\s*'
                               r'<div class="propertyDetailsSectionContentValue">(.*?)</div>', html, re.S):
            v = re.sub(r"<span class='listingCADLabel'.*?</span>", "", v, flags=re.S)
            facts.setdefault(page_text(k).lower(), page_text(v))
        title = page_text(self._rx(html, r"<title>(.*?)</title>") or "")

        # "429 - 50 RICHMOND STREET E<br>Oshawa (O'Neill), Ontario L1G7C7"
        heading = self._rx(html, r'<h1 id="listingAddress"[^>]*>(.*?)</h1>') or ""
        lines = [page_text(x) for x in re.split(r"<br\s*/?>", heading)]
        street = _title_case(lines[0]) if lines and lines[0] else ""
        result["unit"], result["address"] = split_unit(street)
        if not result["address"]:
            errors.append("parse failed for realtor.ca: address")
        place = re.match(r"\s*([^,(]+?)\s*(?:\(([^)]+)\))?\s*,", lines[1]) if len(lines) > 1 else None
        result["city"] = place.group(1).strip() if place else ""
        result["neighbourhood"] = (place.group(2) or facts.get("community name")) if place else facts.get("community name")
        if not result["city"]:
            errors.append("parse failed for realtor.ca: city")
        result["building_name"] = None

        result["mls_number"] = self._rx(html, r'id="MLNumberVal"[^>]*>\s*([A-Z0-9]+)')
        result["list_price"] = money(self._rx(html, r'id="listingPriceValue"[^>]*>(.*?)</div>'))
        if result["list_price"] is None:
            errors.append("parse failed for realtor.ca: list_price")
        lowered = title.lower()
        if lowered.startswith(("for rent", "for lease")):
            result["is_rental"] = True
        result["status"] = (Status.FOR_SALE if lowered.startswith(("for sale", "for rent", "for lease"))
                            else Status.UNKNOWN).value

        result["beds"] = self._safe_int(self._rx(self._icon(html, "BedroomIcon"), r"(\d+)"))
        result["baths"] = self._safe_int(self._rx(self._icon(html, "BathroomIcon"), r"(\d+)"))
        result["sqft_min"], result["sqft_max"] = sqft_range(facts.get("square footage") or self._icon(html, "SquareFootageIcon"))
        result["property_type"] = property_kind(facts.get("building type"), facts.get("title", ""))
        result["taxes_annual"] = money(facts.get("annual property taxes"))
        result["maintenance_fee"] = money(facts.get("maintenance fees"))
        result["fee_includes"] = facts.get("maintenance fees include")
        spaces = self._safe_int(self._rx(facts.get("total parking spaces") or "", r"(\d+)"))
        result["parking"] = (f"{spaces} space" + ("s" if spaces != 1 else "")) if spaces else None
        features = (facts.get("features") or "").lower()
        if "in suite laundry" in features or "ensuite laundry" in features:
            result["ensuite_laundry"] = "Yes"
        if "balcony" in features:
            result["outdoor_space"] = "Balcony"
        result["broker"] = page_text(self._rx(html, r'class="officeCardName"[^>]*>(.*?)</div>') or "") or None
        result["days_on_market"] = _days(facts.get("time on realtor.ca"))
        if self._detect_power_of_sale(page_text(self._rx(html, r'id="propertyDescriptionCon"[^>]*>(.*?)</div>') or "")):
            result["power_of_sale"] = True
        result["parse_errors"] = "; ".join(errors) if errors else None
        return result

    def _icon(self, html: str, icon_id: str) -> str:
        """The number shown with one of the header icons ("3 + 1" bedrooms)."""
        return page_text(self._rx(html, rf'id="{icon_id}".*?class="listingIconNum"[^>]*>(.*?)</div>') or "")

    def _rx(self, text: str, pattern: str, default=None) -> Optional[str]:
        m = re.search(pattern, text or "", re.IGNORECASE | re.S)
        return m.group(1).strip() if m else default


def _title_case(street: str) -> str:
    """"50 RICHMOND STREET E" -> "50 Richmond Street E", keeping compass points and unit letters."""
    words = []
    for w in street.split():
        words.append(w if (len(w) <= 2 and w.isalpha() and w.upper() in ("N", "S", "E", "W", "NE", "NW", "SE", "SW"))
                     or any(c.isdigit() for c in w) else w.capitalize())
    return " ".join(words)


def _days(text: Optional[str]) -> Optional[int]:
    """Days from "16 hours", "5 days" or "2 months"."""
    m = re.match(r"\s*(\d+)\s*(hour|day|week|month|year)", text or "", re.I)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    return {"hour": 0, "day": n, "week": n * 7, "month": n * 30, "year": n * 365}[unit]

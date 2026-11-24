"""Reader for rew.ca listing pages.

REW labels its header with data fields (data-listing-city='Oshawa', data-listing-num-bedrooms='2',
data-listing-sqft='1400 - 1598') and lays out the rest as <section><div>Label</div><div>Value</div>
</section>. Its structured data calls the neighbourhood the "addressLocality", so the town comes
from the header instead. REW has no sold prices or listing history for Ontario.
"""

from __future__ import annotations

import re
import logging
from typing import Optional
from urllib.parse import urlparse

from listingtracker.adapters.base import (
    SiteAdapter, money, page_text, property_kind, split_unit, sqft_range,
)
from listingtracker.models import Status

logger = logging.getLogger(__name__)

_STATUSES = {"for sale": Status.FOR_SALE, "sold": Status.SOLD, "for rent": Status.FOR_SALE,
             "for lease": Status.FOR_SALE, "leased": Status.SOLD}


class RewAdapter(SiteAdapter):
    site_name = "rew.ca"
    domain_pattern = r"rew\.ca"

    @classmethod
    def is_listing_url(cls, url: str) -> bool:
        """/properties/{unit-}{street address}-{town}-on, but not /properties/areas/..."""
        parts = urlparse(url).path.strip("/").split("/")
        return len(parts) == 2 and parts[0] == "properties" and bool(re.match(r"[a-z]{0,3}\d", parts[1]))

    def parse(self, html: str, url: str = "") -> dict:
        errors = []
        result: dict = {}
        facts: dict[str, str] = {}
        for k, v in re.findall(r"<section><div>([^<]{1,60})</div>(.*?)</section>", html, re.S):
            facts.setdefault(page_text(k).lower(), page_text(v).replace("Get Pre-Approved", "").strip())
        title = page_text(self._rx(html, r"<title>(.*?)</title>") or "")

        # data-listing-address='1003-900 Wilson Road N, Oshawa ON, L1G 7T2'
        full = page_text(self._rx(html, r"data-listing-address='([^']*)'") or "")
        street = full.split(",")[0] if full else (self._rx(html, r'"streetAddress"\s*:\s*"([^"]+)"') or "")
        result["unit"], result["address"] = split_unit(street)
        if not result["address"]:
            errors.append("parse failed for rew.ca: address")
        result["city"] = page_text(self._rx(html, r"data-listing-city='([^']*)'") or "")
        if not result["city"]:
            errors.append("parse failed for rew.ca: city")
        result["neighbourhood"] = (page_text(self._rx(html, r"data-listing-neighborhood='([^']*)'") or "")
                                   or facts.get("community"))
        result["building_name"] = None

        # "For Sale: 1003-900 Wilson Road N, Oshawa, ON - REW"
        status_word = (self._rx(title, r"^([A-Za-z ]+):") or "").lower()
        result["status"] = _STATUSES.get(status_word, Status.UNKNOWN).value
        if status_word in ("for rent", "for lease", "leased"):
            result["is_rental"] = True
        result["list_price"] = money(facts.get("list price") or self._rx(html, r"class='scrolledmenu-title'>([^<]+)<"))
        if result["list_price"] is None:
            errors.append("parse failed for rew.ca: list_price")

        result["mls_number"] = next((v for k, v in facts.items() if k.startswith("mls")), None)
        result["beds"] = self._safe_int(self._rx(html, r"data-listing-num-bedrooms='(\d+)"))
        result["baths"] = self._safe_int(self._rx(html, r"data-listing-num-bathrooms='(\d+)"))
        result["sqft_min"], result["sqft_max"] = sqft_range(self._rx(html, r"data-listing-sqft='([^']*)'"))
        result["property_type"] = property_kind(facts.get("property type"), facts.get("title", ""))
        result["taxes_annual"] = money(next((v for k, v in facts.items() if k.startswith("gross taxes")), None))
        result["maintenance_fee"] = money(next((v for k, v in facts.items() if "maintenance fee" in k), None))
        spaces = self._safe_int(self._rx(facts.get("parking spaces") or "", r"(\d+)"))
        result["parking"] = (f"{spaces} space" + ("s" if spaces != 1 else "")) if spaces else None
        result["broker"] = facts.get("primary broker")
        result["days_on_market"] = self._safe_int(self._rx(facts.get("days on rew") or "", r"(\d+)"))
        features = " ".join(v for k, v in facts.items() if "feature" in k or "amenit" in k).lower()
        if "in suite laundry" in features or "ensuite laundry" in features:
            result["ensuite_laundry"] = "Yes"
        if self._detect_power_of_sale(page_text(self._rx(html, r"About this home(.*?)Listed by") or "")):
            result["power_of_sale"] = True
        result["parse_errors"] = "; ".join(errors) if errors else None
        return result

    def _rx(self, text: str, pattern: str, default=None) -> Optional[str]:
        m = re.search(pattern, text or "", re.IGNORECASE | re.S)
        return m.group(1).strip() if m else default

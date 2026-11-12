"""Adapter for rew.ca listings."""

from __future__ import annotations

import re
import json
import logging
from typing import Optional

from listingtracker.adapters.base import SiteAdapter
from listingtracker.models import Status

logger = logging.getLogger(__name__)


class RewAdapter(SiteAdapter):
    site_name = "rew.ca"
    domain_pattern = r"rew\.ca"

    def parse(self, html: str, url: str = "") -> dict:
        errors = []
        result = {}

        ld = self._extract_ld_json(html)

        result["address"] = (
            ld.get("address", {}).get("streetAddress", "")
            if ld else self._rx(html, r'"streetAddress"\s*:\s*"([^"]+)"', "")
        )
        if not result["address"]:
            m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
            result["address"] = m.group(1).strip() if m else ""
            if not result["address"]:
                errors.append("parse failed for rew.ca: address")

        result["city"] = (
            ld.get("address", {}).get("addressLocality", "")
            if ld else self._rx(html, r'"addressLocality"\s*:\s*"([^"]+)"', "")
        )
        if not result["city"]:
            errors.append("parse failed for rew.ca: city")

        result["unit"] = self._rx(html, r'(?:Unit|Suite|Apt|#)\s*(\d+\w*)')
        result["building_name"] = None
        result["neighbourhood"] = self._rx(html, r'"neighborhood"\s*:\s*"([^"]+)"')
        result["mls_number"] = self._rx(html, r'MLS[®#:\s]*([A-Z]\d{7,})')
        result["status"] = self._extract_status(html, errors)
        result["list_price"] = self._extract_price(html, ld, errors)
        result["maintenance_fee"] = self._safe_float(
            self._rx(html, r'(?:Maintenance|Strata|Condo)\s*Fee[^$]*\$([\d,]+(?:\.\d{2})?)')
        )
        result["taxes_annual"] = self._safe_float(
            self._rx(html, r'(?:Property\s*)?Tax(?:es)?[^$]*\$([\d,]+(?:\.\d{2})?)')
        )
        result["beds"] = self._safe_int(self._rx(html, r'(\d+)\s*[Bb]ed'))
        result["baths"] = self._safe_int(self._rx(html, r'(\d+)\s*[Bb]ath'))
        sqft = self._extract_sqft(html)
        result["sqft_min"] = sqft[0]
        result["sqft_max"] = sqft[1]
        result["parking"] = self._sanitize_field(
            self._rx(html, r'Parking[^:]*:\s*([^<\n]{1,80})'))
        result["locker"] = self._sanitize_field(
            self._rx(html, r'Locker\s*:?\s*(Yes|No|Owned|[^<\n]{1,50})'))
        result["balcony"] = self._sanitize_field(
            self._rx(html, r'Balcony\s*:?\s*([^<\n]{1,50})'))
        result["exposure"] = self._rx(html, r'(?:Exposure|Facing)\s*:?\s*([NSEW][^<\n]{0,20})')
        result["days_on_market"] = self._safe_int(
            self._rx(html, r'(\d+)\s*(?:days?\s*on\s*market|DOM)')
        )
        result["broker"] = self._rx(html, r'(?:Listed by|Brokerage)\s*:?\s*([^<\n]{3,80})')
        result["fee_includes"] = self._rx(html, r'(?:Fee\s*Includes?)\s*:?\s*([^<\n]{3,200})')
        result["site_estimate"] = None
        if self._detect_power_of_sale(html):
            result["power_of_sale"] = True
        result["parse_errors"] = "; ".join(errors) if errors else None
        return result

    def _extract_ld_json(self, html: str) -> Optional[dict]:
        for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict) and "address" in data:
                    return data
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "address" in item:
                            return item
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    def _extract_status(self, html: str, errors: list) -> str:
        if re.search(r'(?:sold|closed)', html[:10000], re.IGNORECASE):
            return Status.SOLD.value
        if re.search(r'\$[\d,]+', html):
            return Status.FOR_SALE.value
        errors.append("parse failed for rew.ca: status")
        return Status.UNKNOWN.value

    def _extract_price(self, html: str, ld: Optional[dict], errors: list) -> Optional[float]:
        if ld and "price" in ld:
            p = self._safe_price(str(ld["price"]))
            if p:
                return p
        for p in [
            r'"price"\s*:\s*"?\$?([\d,]+)"?',
            r'class="[^"]*price[^"]*"[^>]*>\s*\$?([\d,]+)',
            r'(?:Asking|Price)\s*:?\s*\$?([\d,]+)',
        ]:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                return self._safe_price(m.group(1))
        errors.append("parse failed for rew.ca: list_price")
        return None


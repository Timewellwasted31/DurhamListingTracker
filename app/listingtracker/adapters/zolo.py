"""Adapter for zolo.ca listings."""

from __future__ import annotations

import re
import logging
from typing import Optional

from listingtracker.adapters.base import SiteAdapter
from listingtracker.models import Status

logger = logging.getLogger(__name__)


class ZoloAdapter(SiteAdapter):
    site_name = "zolo.ca"
    domain_pattern = r"zolo\.ca"

    def parse(self, html: str, url: str = "") -> dict:
        errors = []
        result = {}

        result["address"] = self._regex(html, r'"streetAddress"\s*:\s*"([^"]+)"', "")
        if not result["address"]:
            m = re.search(r'<h1[^>]*class="[^"]*listing-address[^"]*"[^>]*>([^<]+)</h1>', html)
            if not m:
                m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
            result["address"] = m.group(1).strip() if m else ""
        if not result["address"]:
            errors.append("parse failed for zolo.ca: address")

        result["city"] = self._regex(html, r'"addressLocality"\s*:\s*"([^"]+)"', "")
        if not result["city"]:
            errors.append("parse failed for zolo.ca: city")

        result["unit"] = self._regex(html, r'"unitNumber"\s*:\s*"([^"]+)"')
        if not result["unit"]:
            result["unit"] = self._regex(html, r'(?:Unit|Suite|#)\s*(\d+\w*)')
        result["building_name"] = self._regex(html, r'class="[^"]*building[^"]*"[^>]*>([^<]+)<')
        result["neighbourhood"] = self._regex(html, r'"neighborhood"\s*:\s*"([^"]+)"')
        result["mls_number"] = self._regex(html, r'MLS[®#:\s]*([A-Z]\d{7,})')
        result["status"] = self._extract_status(html, errors)
        result["list_price"] = self._extract_price(html, errors)
        result["maintenance_fee"] = self._safe_float(
            self._regex(html, r'(?:Maintenance|Condo)\s*Fee[^$]*\$([\d,]+(?:\.\d{2})?)')
        )
        result["taxes_annual"] = self._safe_float(
            self._regex(html, r'Tax(?:es)?[^$]*\$([\d,]+(?:\.\d{2})?)')
        )
        result["beds"] = self._safe_int(self._regex(html, r'(\d+)\s*[Bb]ed'))
        result["baths"] = self._safe_int(self._regex(html, r'(\d+)\s*[Bb]ath'))

        sqft = self._extract_sqft(html)
        result["sqft_min"] = sqft[0]
        result["sqft_max"] = sqft[1]
        result["parking"] = self._sanitize_field(
            self._regex(html, r'Parking\s*:?\s*([^<\n]{1,80})'))
        result["locker"] = self._sanitize_field(
            self._regex(html, r'Locker\s*:?\s*(Yes|No|Owned|[^<\n]{1,50})'))
        result["balcony"] = self._sanitize_field(
            self._regex(html, r'Balcony\s*:?\s*([^<\n]{1,50})'))
        result["exposure"] = self._regex(html, r'(?:Exposure|Facing)\s*:?\s*([NSEW][^<\n]{0,20})')
        result["days_on_market"] = self._safe_int(
            self._regex(html, r'(\d+)\s*(?:days?\s*on\s*market|DOM)')
        )
        result["site_estimate"] = self._safe_float(
            self._regex(html, r'(?:Zolo\s*Estimate|Home Value)\s*:?\s*\$([\d,]+)')
        )
        result["broker"] = self._regex(html, r'(?:Listed by|Brokerage)\s*:?\s*([^<\n]{3,80})')
        result["fee_includes"] = self._regex(html, r'(?:Fee\s*Includes?)\s*:?\s*([^<\n]{3,200})')
        if self._detect_power_of_sale(html):
            result["power_of_sale"] = True
        result["parse_errors"] = "; ".join(errors) if errors else None
        return result

    def _regex(self, html: str, pattern: str, default=None) -> Optional[str]:
        m = re.search(pattern, html, re.IGNORECASE)
        return m.group(1).strip() if m else default

    def _extract_status(self, html: str, errors: list) -> str:
        m = re.search(r'"listing[_-]?status"\s*:\s*"([^"]+)"', html, re.IGNORECASE)
        if m:
            return Status.normalize(m.group(1)).value
        if re.search(r'class="[^"]*sold[^"]*"', html, re.IGNORECASE):
            return Status.SOLD.value
        if re.search(r'\$[\d,]+', html):
            return Status.FOR_SALE.value
        errors.append("parse failed for zolo.ca: status")
        return Status.UNKNOWN.value

    def _extract_price(self, html: str, errors: list) -> Optional[float]:
        for p in [
            r'"price"\s*:\s*"?\$?([\d,]+)"?',
            r'class="[^"]*price[^"]*"[^>]*>\s*\$?([\d,]+)',
            r'(?:Asking|Price)\s*:?\s*\$?([\d,]+)',
        ]:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                return self._safe_price(m.group(1))
        errors.append("parse failed for zolo.ca: list_price")
        return None


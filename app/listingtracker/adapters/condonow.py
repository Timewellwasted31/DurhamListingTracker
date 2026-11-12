"""Adapter for condonow.com listings."""

from __future__ import annotations

import re
import logging
from typing import Optional

from listingtracker.adapters.base import SiteAdapter
from listingtracker.models import Status

logger = logging.getLogger(__name__)


class CondoNowAdapter(SiteAdapter):
    site_name = "condonow.com"
    domain_pattern = r"condonow\.com"

    def parse(self, html: str, url: str = "") -> dict:
        errors = []
        result = {}

        result["address"] = self._rx(html, r'"streetAddress"\s*:\s*"([^"]+)"', "")
        if not result["address"]:
            m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
            result["address"] = m.group(1).strip() if m else ""
            if not result["address"]:
                errors.append("parse failed for condonow.com: address")

        result["city"] = self._rx(html, r'"addressLocality"\s*:\s*"([^"]+)"', "")
        if not result["city"]:
            errors.append("parse failed for condonow.com: city")

        result["unit"] = self._rx(html, r'(?:Unit|Suite|#)\s*(\d+\w*)')
        result["building_name"] = self._rx(html, r'<h1[^>]*>([^<]+)</h1>')
        result["neighbourhood"] = self._rx(html, r'"neighborhood"\s*:\s*"([^"]+)"')
        result["mls_number"] = self._rx(html, r'MLS[®#:\s]*([A-Z]\d{7,})')
        result["status"] = self._extract_status(html, errors)
        result["list_price"] = self._extract_price(html, errors)
        result["maintenance_fee"] = self._safe_float(
            self._rx(html, r'(?:Maintenance|Condo)\s*Fee[s]?\s*:?\s*\$([\d,]+(?:\.\d{2})?)')
        )
        result["taxes_annual"] = self._safe_float(
            self._rx(html, r'(?:Property\s*)?Tax(?:es)?[^$]*\$([\d,]+(?:\.\d{2})?)')
        )
        result["beds"] = self._safe_int(self._rx(html, r'(\d+)\s*[Bb]ed'))
        result["baths"] = self._safe_int(self._rx(html, r'(\d+)\s*[Bb]ath'))
        sqft = self._extract_sqft(html)
        result["sqft_min"] = sqft[0]
        result["sqft_max"] = sqft[1]
        result["parking"] = self._rx(html, r'Parking\s*:?\s*(\d+)')
        result["locker"] = self._sanitize_field(
            self._rx(html, r'Locker\s*:?\s*(Yes|No|Owned|[^<\n]{1,50})'))
        result["balcony"] = self._sanitize_field(
            self._rx(html, r'Balcony\s*:?\s*([^<\n]{1,50})'))
        result["exposure"] = self._rx(html, r'(?:Exposure|Facing)\s*:?\s*([NSEW][^<\n]{0,20})')
        result["days_on_market"] = self._safe_int(
            self._rx(html, r'(\d+)\s*(?:days?\s*on\s*market|DOM)')
        )
        result["broker"] = self._rx(html, r'(?:Listed by|Brokerage)\s*:?\s*([^<\n]{3,80})')
        result["fee_includes"] = self._rx(html, r'(?:Fee\s*Includes?|Includes)\s*:?\s*([^<\n]{3,200})')
        result["site_estimate"] = None
        if self._detect_power_of_sale(html):
            result["power_of_sale"] = True
        result["parse_errors"] = "; ".join(errors) if errors else None
        return result

    def _extract_status(self, html: str, errors: list) -> str:
        if re.search(r'(?:sold|closed)', html[:10000], re.IGNORECASE):
            return Status.SOLD.value
        if re.search(r'\$[\d,]+', html):
            return Status.FOR_SALE.value
        errors.append("parse failed for condonow.com: status")
        return Status.UNKNOWN.value

    def _extract_price(self, html: str, errors: list) -> Optional[float]:
        for p in [
            r'"price"\s*:\s*"?\$?([\d,]+)"?',
            r'class="[^"]*price[^"]*"[^>]*>\s*\$?([\d,]+)',
            r'(?:Asking|Price|Listed at)\s*:?\s*\$?([\d,]+)',
        ]:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                return self._safe_price(m.group(1))
        errors.append("parse failed for condonow.com: list_price")
        return None


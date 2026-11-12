"""Adapter for condos.ca listings."""

from __future__ import annotations

import re
import logging
from typing import Optional

from listingtracker.adapters.base import SiteAdapter
from listingtracker.models import Status

logger = logging.getLogger(__name__)


class CondosAdapter(SiteAdapter):
    site_name = "condos.ca"
    domain_pattern = r"condos\.ca"

    @classmethod
    def is_listing_url(cls, url: str) -> bool:
        """condos.ca listing URLs contain /unit- in the path."""
        from urllib.parse import urlparse
        path = urlparse(url).path.lower()
        return "/unit-" in path

    def parse(self, html: str, url: str = "") -> dict:
        errors = []
        result = {}

        result["address"] = self._extract(
            html, errors, "address",
            r'"streetAddress"\s*:\s*"([^"]+)"',
            r'<h1[^>]*>([^<]*\d+[^<]*(?:St|Ave|Rd|Dr|Blvd|Crt|Cres)[^<]*)</h1>',
        )
        result["unit"] = self._extract(
            html, errors, None,
            r'"unitNumber"\s*:\s*"([^"]+)"',
            r'(?:Unit|Suite|#)\s*(\d+\w*)',
        )
        result["building_name"] = self._extract(
            html, errors, None,
            r'<[^>]*class="[^"]*building.?name[^"]*"[^>]*>([^<]+)<',
        )
        result["city"] = self._extract(
            html, errors, "city",
            r'"addressLocality"\s*:\s*"([^"]+)"',
        )
        result["neighbourhood"] = self._extract(html, errors, None, r'"neighborhood"\s*:\s*"([^"]+)"')
        result["mls_number"] = self._extract(
            html, errors, None,
            r'MLS[®#:\s]*([A-Z]\d{7,})',
        )
        result["status"] = self._extract_status(html, errors)
        result["list_price"] = self._extract_price_val(html, errors)
        result["maintenance_fee"] = self._safe_float(
            self._extract(html, errors, None,
                          r'(?:Maintenance|Condo)\s*Fee[^$]*\$([\d,]+(?:\.\d{2})?)')
        )
        result["taxes_annual"] = self._safe_float(
            self._extract(html, errors, None, r'Tax(?:es)?[^$]*\$([\d,]+(?:\.\d{2})?)')
        )
        result["beds"] = self._safe_int(
            self._extract(html, errors, None, r'(\d+)\s*[Bb]ed')
        )
        result["baths"] = self._safe_int(
            self._extract(html, errors, None, r'(\d+)\s*[Bb]ath')
        )
        sqft = self._extract_sqft(html)
        result["sqft_min"] = sqft[0]
        result["sqft_max"] = sqft[1]
        result["parking"] = self._sanitize_field(
            self._extract(html, errors, None, r'Parking\s*:?\s*([^<\n]{1,80})'))
        result["locker"] = self._sanitize_field(
            self._extract(html, errors, None, r'Locker\s*:?\s*(Yes|No|Owned|[^<\n]{1,50})'))
        result["balcony"] = self._sanitize_field(
            self._extract(html, errors, None, r'Balcony\s*:?\s*([^<\n]{1,50})'))
        result["exposure"] = self._extract(html, errors, None, r'(?:Exposure|Facing)\s*:?\s*([NSEW][^<\n]{0,20})')
        result["days_on_market"] = self._safe_int(
            self._extract(html, errors, None, r'(\d+)\s*(?:days?\s*on\s*market|DOM)')
        )
        result["broker"] = self._extract(html, errors, None, r'(?:Listed by|Brokerage)\s*:?\s*([^<\n]{3,80})')
        result["fee_includes"] = self._extract(html, errors, None, r'(?:Fee\s*Includes?|Included)\s*:?\s*([^<\n]{3,200})')
        result["year_built"] = self._safe_int(
            self._extract(html, errors, None,
                          r'"yearBuilt"\s*:\s*"?(\d{4})"?',
                          r'(?:Year\s*Built|Built\s*in|Completed)\s*:?\s*(\d{4})')
        )
        result["site_estimate"] = None
        if self._detect_power_of_sale(html):
            result["power_of_sale"] = True
        result["parse_errors"] = "; ".join(errors) if errors else None
        return result

    def _extract(self, html: str, errors: list, field_name: Optional[str], *patterns: str) -> Optional[str]:
        for p in patterns:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        if field_name:
            errors.append(f"parse failed for condos.ca: {field_name}")
        return "" if field_name else None

    def _extract_status(self, html: str, errors: list) -> str:
        m = re.search(r'"listing[_-]?status"\s*:\s*"([^"]+)"', html, re.IGNORECASE)
        if m:
            return Status.normalize(m.group(1)).value
        if re.search(r'class="[^"]*sold[^"]*"', html, re.IGNORECASE):
            return Status.SOLD.value
        if re.search(r'\$[\d,]+', html):
            return Status.FOR_SALE.value
        errors.append("parse failed for condos.ca: status")
        return Status.UNKNOWN.value

    def _extract_price_val(self, html: str, errors: list) -> Optional[float]:
        patterns = [
            r'"price"\s*:\s*"?\$?([\d,]+)"?',
            r'class="[^"]*price[^"]*"[^>]*>\s*\$?([\d,]+)',
        ]
        for p in patterns:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                return self._safe_price(m.group(1))
        errors.append("parse failed for condos.ca: list_price")
        return None


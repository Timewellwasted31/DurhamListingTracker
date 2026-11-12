"""Adapter for housesigma.com listings."""

from __future__ import annotations

import re
import json
import logging
from typing import Optional

from listingtracker.adapters.base import SiteAdapter
from listingtracker.models import Status

logger = logging.getLogger(__name__)


class HouseSigmaAdapter(SiteAdapter):
    site_name = "housesigma.com"
    domain_pattern = r"housesigma\.com"

    @classmethod
    def is_listing_url(cls, url: str) -> bool:
        """HouseSigma listing URLs contain /home/ with a listing ID."""
        from urllib.parse import urlparse
        path = urlparse(url).path.lower()
        return "/home/" in path or "/listing/" in path

    def parse(self, html: str, url: str = "") -> dict:
        errors = []
        result = {}

        result["address"] = self._regex(html, r'"address"\s*:\s*"([^"]+)"', "")
        if not result["address"]:
            m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
            result["address"] = m.group(1).strip() if m else ""
        if not result["address"]:
            errors.append("parse failed for housesigma.com: address")

        result["city"] = self._regex(html, r'"city"\s*:\s*"([^"]+)"', "")
        if not result["city"]:
            result["city"] = self._regex(html, r'"addressLocality"\s*:\s*"([^"]+)"', "")
        if not result["city"]:
            errors.append("parse failed for housesigma.com: city")

        result["unit"] = self._regex(html, r'"unit(?:Number)?"\s*:\s*"([^"]+)"')
        result["building_name"] = None
        result["neighbourhood"] = self._regex(html, r'"community"\s*:\s*"([^"]+)"')
        result["mls_number"] = self._regex(html, r'(?:MLS|ml_num)[®#:\s]*"?([A-Z]\d{7,})"?')
        result["status"] = self._extract_status(html, errors)
        result["list_price"] = self._extract_price(html, errors)
        result["maintenance_fee"] = self._safe_float(
            self._regex(html, r'(?:Maintenance|Condo)\s*Fee[^$]*\$([\d,]+(?:\.\d{2})?)')
        )
        result["taxes_annual"] = self._safe_float(
            self._regex(html, r'Tax(?:es)?[^$]*\$([\d,]+(?:\.\d{2})?)')
        )
        result["beds"] = self._safe_int(self._regex(html, r'"bed(?:room)?s?"\s*:\s*"?(\d+)"?'))
        if result["beds"] is None:
            result["beds"] = self._safe_int(self._regex(html, r'(\d+)\s*[Bb]ed'))
        result["baths"] = self._safe_int(self._regex(html, r'"bath(?:room)?s?"\s*:\s*"?(\d+)"?'))
        if result["baths"] is None:
            result["baths"] = self._safe_int(self._regex(html, r'(\d+)\s*[Bb]ath'))

        sqft = self._extract_sqft(html)
        result["sqft_min"] = sqft[0]
        result["sqft_max"] = sqft[1]
        result["parking"] = self._sanitize_field(
            self._regex(html, r'Parking[^:]*:\s*([^<\n]{1,80})'))
        result["locker"] = self._sanitize_field(
            self._regex(html, r'Locker\s*:?\s*(Yes|No|Owned|[^<\n]{1,50})'))
        result["balcony"] = None
        result["exposure"] = self._regex(html, r'(?:Exposure|Facing)\s*:?\s*([NSEW][^<\n]{0,20})')
        result["days_on_market"] = self._safe_int(
            self._regex(html, r'(\d+)\s*(?:days?\s*on\s*market|DOM)')
        )
        result["site_estimate"] = self._safe_float(
            self._regex(html, r'(?:Estimate|Estimated Value)\s*:?\s*\$([\d,]+)')
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
        m = re.search(r'"status"\s*:\s*"([^"]+)"', html, re.IGNORECASE)
        if m:
            return Status.normalize(m.group(1)).value
        if re.search(r'class="[^"]*sold[^"]*"', html, re.IGNORECASE):
            return Status.SOLD.value
        if re.search(r'\$[\d,]+', html):
            return Status.FOR_SALE.value
        errors.append("parse failed for housesigma.com: status")
        return Status.UNKNOWN.value

    def _extract_price(self, html: str, errors: list) -> Optional[float]:
        for p in [
            r'"(?:list_?)?price"\s*:\s*"?\$?([\d,]+)"?',
            r'class="[^"]*price[^"]*"[^>]*>\s*\$?([\d,]+)',
        ]:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                return self._safe_price(m.group(1))
        errors.append("parse failed for housesigma.com: list_price")
        return None

    def _extract_sqft(self, html: str) -> tuple[Optional[int], Optional[int]]:
        m = re.search(r'(\d[\d,]*)\s*[-–]\s*(\d[\d,]*)\s*(?:sq\.?\s*ft|sqft|sf)', html, re.IGNORECASE)
        if m:
            return self._safe_int(m.group(1)), self._safe_int(m.group(2))
        m = re.search(r'"sqft"\s*:\s*"?(\d[\d,]*)"?', html, re.IGNORECASE)
        if m:
            v = self._safe_int(m.group(1))
            return v, v
        m = re.search(r'(\d[\d,]*)\s*(?:sq\.?\s*ft|sqft|sf)', html, re.IGNORECASE)
        if m:
            v = self._safe_int(m.group(1))
            return v, v
        return None, None

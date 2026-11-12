"""Adapter for wahi.com listings."""

from __future__ import annotations

import re
import json
import logging
from typing import Optional

from listingtracker.adapters.base import SiteAdapter
from listingtracker.models import Status

logger = logging.getLogger(__name__)


class WahiAdapter(SiteAdapter):
    site_name = "wahi.com"
    domain_pattern = r"wahi\.com"

    @classmethod
    def is_listing_url(cls, url: str) -> bool:
        """wahi.com listing URLs have a postal-code-like segment (6+ alphanumeric) in the last path part."""
        import re as _re
        from urllib.parse import urlparse
        path = urlparse(url).path.rstrip("/")
        parts = path.strip("/").split("/")
        if len(parts) < 6:
            return False
        # Listing URLs end with an address slug containing a postal code
        last = parts[-1]
        return bool(_re.search(r'[a-z]\d[a-z]\d[a-z]\d', last, _re.IGNORECASE))

    def parse(self, html: str, url: str = "") -> dict:
        errors = []
        result = {}

        nd = self._extract_next_data(html)
        listing = nd.get("props", {}).get("pageProps", {}).get("listing", {}) if nd else {}
        ld = self._extract_ld_json(html)

        if listing:
            addr = listing.get("address", {})
            parts = [addr.get("streetNumber", ""), addr.get("streetName", ""),
                      addr.get("streetSuffix", ""), addr.get("streetDirection", "")]
            result["address"] = " ".join(p for p in parts if p).strip()
            result["city"] = addr.get("city", "")
            result["unit"] = addr.get("unitNumber") or None
            result["mls_number"] = listing.get("mlsId", "")
            result["list_price"] = self._safe_float(str(listing.get("listPrice", "")))
            result["maintenance_fee"] = self._safe_float(str(listing.get("maintenanceFees", "")))
            taxes = listing.get("taxes", {})
            result["taxes_annual"] = self._safe_float(str(taxes.get("annualAmount", "")))
            details = listing.get("details", {})
            result["beds"] = self._safe_int(str(details.get("numBedrooms", "")))
            result["baths"] = self._safe_int(str(details.get("numBathrooms", "")))
            sqft_str = details.get("sqft", "")
            if sqft_str and "-" in str(sqft_str):
                lo, hi = str(sqft_str).split("-", 1)
                result["sqft_min"] = self._safe_int(lo)
                result["sqft_max"] = self._safe_int(hi)
            elif sqft_str:
                v = self._safe_int(str(sqft_str))
                result["sqft_min"] = v
                result["sqft_max"] = v
            else:
                result["sqft_min"] = None
                result["sqft_max"] = None
            result["days_on_market"] = self._safe_int(str(listing.get("daysOnMarket", "")))
            result["broker"] = listing.get("brokerageName")
            status_raw = listing.get("status", "")
            if status_raw == "A":
                result["status"] = Status.FOR_SALE.value
            elif status_raw in ("U", "S"):
                result["status"] = Status.SOLD.value
            else:
                result["status"] = Status.normalize(status_raw).value
        else:
            result["address"] = (
                ld.get("address", {}).get("streetAddress", "")
                if ld else self._rx(html, r'"streetAddress"\s*:\s*"([^"]+)"', "")
            )
            result["city"] = (
                ld.get("address", {}).get("addressLocality", "")
                if ld else self._rx(html, r'"addressLocality"\s*:\s*"([^"]+)"', "")
            )
            result["unit"] = self._rx(html, r'(?:Unit|Suite|Apt|#)\s*(\d+\w*)')
            result["mls_number"] = self._rx(html, r'MLS[®#:\s]*([A-Z]\d{7,})')
            result["status"] = self._extract_status(html, errors)
            result["list_price"] = self._extract_price(html, ld, errors)
            result["maintenance_fee"] = self._safe_float(
                self._rx(html, r'(?:Maintenance|Condo)\s*Fee[^$]*\$([\d,]+(?:\.\d{2})?)')
            )
            result["taxes_annual"] = self._safe_float(
                self._rx(html, r'(?:Property\s*)?Tax(?:es)?[^$]*\$([\d,]+(?:\.\d{2})?)')
            )
            result["beds"] = self._safe_int(self._rx(html, r'(\d+)\s*[Bb]ed'))
            result["baths"] = self._safe_int(self._rx(html, r'(\d+)\s*[Bb]ath'))
            sqft = self._extract_sqft(html)
            result["sqft_min"] = sqft[0]
            result["sqft_max"] = sqft[1]
            result["days_on_market"] = self._safe_int(
                self._rx(html, r'(\d+)\s*(?:days?\s*on\s*market|DOM)')
            )
            result["broker"] = self._rx(html, r'(?:Listed by|Brokerage)\s*:?\s*([^<\n]{3,80})')

        if not result.get("address"):
            errors.append("parse failed for wahi.com: address")
        if not result.get("city"):
            errors.append("parse failed for wahi.com: city")

        result.setdefault("building_name", None)
        result.setdefault("neighbourhood", self._rx(html, r'"neighborhood"\s*:\s*"([^"]+)"'))
        result.setdefault("parking", self._sanitize_field(
            self._rx(html, r'Parking[^:]*:\s*([^<\n]{1,80})')))
        result.setdefault("locker", self._sanitize_field(
            self._rx(html, r'Locker\s*:?\s*(Yes|No|Owned|[^<\n]{1,50})')))
        result.setdefault("balcony", self._sanitize_field(
            self._rx(html, r'Balcony\s*:?\s*([^<\n]{1,50})')))
        result.setdefault("exposure", self._rx(html, r'(?:Exposure|Facing)\s*:?\s*([NSEW][^<\n]{0,20})'))
        result.setdefault("fee_includes", self._rx(html, r'(?:Fee\s*Includes?)\s*:?\s*([^<\n]{3,200})'))
        result.setdefault("site_estimate", None)
        if self._detect_power_of_sale(html):
            result["power_of_sale"] = True
        result["parse_errors"] = "; ".join(errors) if errors else None
        return result

    def _extract_next_data(self, html: str) -> Optional[dict]:
        m = re.search(r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    def _extract_ld_json(self, html: str) -> Optional[dict]:
        for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict) and data.get("@type") in ("Product", "RealEstateListing", "Residence"):
                    return data
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("@type") in ("Product", "RealEstateListing", "Residence"):
                            return item
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    def _extract_status(self, html: str, errors: list) -> str:
        if re.search(r'(?:sold|closed)', html[:10000], re.IGNORECASE):
            return Status.SOLD.value
        if re.search(r'\$[\d,]+', html):
            return Status.FOR_SALE.value
        errors.append("parse failed for wahi.com: status")
        return Status.UNKNOWN.value

    def _extract_price(self, html: str, ld: Optional[dict], errors: list) -> Optional[float]:
        if ld:
            for key in ("price", "offers"):
                val = ld.get(key)
                if isinstance(val, dict):
                    val = val.get("price")
                if val:
                    p = self._safe_price(str(val))
                    if p:
                        return p
        for p in [
            r'"price"\s*:\s*"?\$?([\d,]+)"?',
            r'class="[^"]*price[^"]*"[^>]*>\s*\$?([\d,]+)',
            r'(?:Asking|List\s*Price|Price)\s*:?\s*\$?([\d,]+)',
        ]:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                return self._safe_price(m.group(1))
        errors.append("parse failed for wahi.com: list_price")
        return None


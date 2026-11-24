"""Reader for zolo.ca listing pages.

A Zolo listing lays out its facts as a labelled list (<dt>Taxes</dt><dd>$5,473 /yr</dd>), with the
address in the heading, the town and neighbourhood under it, and the price and status in the page
title. The page also carries data about other listings (open houses nearby), so nothing is taken
from anywhere else on the page.
"""

from __future__ import annotations

import re
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from listingtracker.adapters.base import (
    SiteAdapter, days_since, money, page_text, property_kind, split_unit, sqft_range,
)
from listingtracker.models import Status

logger = logging.getLogger(__name__)

# Zolo pages under a town that aren't listings: "/oshawa-real-estate/1-bedroom-apartments-for-rent"
_NOT_A_LISTING = re.compile(
    r"for-(?:rent|sale|lease)|apartments|bedroom|houses|condos|townhouses|open-houses|new-listings"
    r"|sold|price-|under-|over-|map\b")

_STATUSES = {
    "for sale": Status.FOR_SALE, "sold": Status.SOLD, "sold conditional": Status.SOLD_CONDITIONAL,
    "expired": Status.EXPIRED, "terminated": Status.TERMINATED, "suspended": Status.DELISTED,
    "delisted": Status.DELISTED, "for rent": Status.FOR_SALE, "for lease": Status.FOR_SALE,
    "leased": Status.SOLD, "rented": Status.SOLD,
}


class ZoloAdapter(SiteAdapter):
    site_name = "zolo.ca"
    domain_pattern = r"zolo\.ca"

    @classmethod
    def is_listing_url(cls, url: str) -> bool:
        """/{town}-real-estate/{street address}[/{unit}]."""
        parts = urlparse(url).path.strip("/").split("/")
        return (len(parts) in (2, 3) and parts[0].endswith("-real-estate")
                and bool(re.match(r"\d+[a-z]?-[a-z]", parts[1])) and not _NOT_A_LISTING.search(parts[1])
                and (len(parts) == 2 or bool(re.fullmatch(r"[a-z]{0,3}\d+[a-z]?", parts[2]))))

    def parse(self, html: str, url: str = "") -> dict:
        errors = []
        result: dict = {}
        # The summary facts (<dt>/<dd>), then the full list under "Details" (<div>/<div>)
        facts = {page_text(k).rstrip(":").lower(): page_text(v) for k, v in re.findall(
            r'<dt class="column-label">(.*?)</dt>\s*<dd class="column-value">(.*?)</dd>', html, re.S)}
        details: dict[str, str] = {}
        for k, v in re.findall(r'<div class="column-label">(.*?)</div>\s*<div class="column-value">(.*?)</div>',
                               html, re.S):
            details.setdefault(page_text(k).lower(), page_text(v))
        title = page_text(self._rx(html, r"<title>(.*?)</title>") or "")
        description = page_text(self._rx(html, r'<meta name="description" content="([^"]*)"') or "")

        heading = page_text(self._rx(html, r'<h1 class="address[^"]*">(.*?)</h1>') or "")
        result["unit"], result["address"] = split_unit(heading)
        if not result["address"]:
            errors.append("parse failed for zolo.ca: address")
        area = re.search(r'<div class="area[^"]*">(.*?)</div>', html, re.S)
        places = [page_text(a) for a in re.findall(r"<a [^>]*>(.*?)</a>", area.group(1))] if area else []
        result["city"] = places[0] if places else ""
        result["neighbourhood"] = places[1] if len(places) > 1 else None
        if not result["city"]:
            errors.append("parse failed for zolo.ca: city")
        result["building_name"] = None

        # "1018 Pinetree Court, Oshawa — For Sale @ $780,000 | Zolo.ca"
        m = re.search(r"[—–-]\s*([A-Za-z ]+?)\s*@\s*\$([\d,]+)", title)
        status_word = m.group(1).strip().lower() if m else ""
        result["status"] = _STATUSES.get(status_word, Status.UNKNOWN).value
        result["list_price"] = float(m.group(2).replace(",", "")) if m else None
        if result["list_price"] is None:
            errors.append("parse failed for zolo.ca: list_price")
        if status_word in ("for rent", "for lease", "leased", "rented"):
            result["is_rental"] = True

        result["mls_number"] = (self._first(facts, "mls®#", "mls#", "mls® #")
                                or self._rx(description, r"MLS#?\s*([A-Z]\d{6,})"))
        result["beds"] = self._safe_int(self._rx(description, r"(\d+)\s*bed"))
        result["baths"] = self._safe_int(self._rx(description, r"(\d+)\s*bath"))
        result["sqft_min"], result["sqft_max"] = sqft_range(facts.get("size") or self._rx(description, r"([\d,-]+)\s*sqft"))
        result["property_type"] = property_kind(facts.get("type"))
        result["taxes_annual"] = money(facts.get("taxes"))
        result["maintenance_fee"] = money(self._first(facts, "maintenance fees", "maintenance fee", "condo fees"))
        result["fee_includes"] = self._first(facts, "fees include", "maintenance fees include")
        spaces = self._safe_int(self._rx(details.get("parking total") or details.get("parking places") or "", r"(\d+)"))
        result["parking"] = (f"{spaces} space" + ("s" if spaces != 1 else "")) if spaces else None
        locker = details.get("lockers")
        result["locker"] = None if not locker or locker.lower() == "none" else locker
        result["exposure"] = details.get("unit exposure")
        patio = details.get("patio terrace") or details.get("balcony")
        result["outdoor_space"] = {"open": "Balcony", "none": "None", "terr": "Terrace", "juliette": "Juliet Balcony",
                                   "encl": "Enclosed/Solarium"}.get((patio or "").lower(), patio)
        laundry = (details.get("ensuite laundry") or "").upper()
        result["ensuite_laundry"] = {"Y": "Yes", "N": "No"}.get(laundry)
        result["broker"] = self._first(facts, "listed by", "brokerage")
        result["site_estimate"] = money(self._first(facts, "zolo estimate", "estimate"))

        # "Added: Nov 20, 2026 (3 days ago)"
        added = self._rx(facts.get("added") or "", r"([A-Z][a-z]{2} \d{1,2}, \d{4})")
        if added:
            try:
                result["list_date"] = datetime.strptime(added, "%b %d, %Y").date().isoformat()
            except ValueError:
                pass
        result["days_on_market"] = days_since(result.get("list_date"))
        self._history(html, result)
        if self._detect_power_of_sale(description):
            result["power_of_sale"] = True
        result["parse_errors"] = "; ".join(errors) if errors else None
        return result

    @staticmethod
    def _history(html: str, result: dict) -> None:
        """The "Sold History" table: this listing's own events give its price history, and earlier
        listings of the same home that sold give its past sales. Zolo masks the earlier ones
        ("Xxx XX, XXXX") unless the person is signed in, and masked rows are skipped."""
        section = re.search(r'id="sold-history".*?</table>', html, re.S)
        if not section:
            return
        rows, mls = [], None
        for row in re.findall(r'<tr class="table-entry">(.*?)</tr>', section.group(0), re.S):
            cells = dict(re.findall(r'<td class="table-(mls|date|event|price)[^"]*">(.*?)</td>', row, re.S))
            mls = page_text(cells.get("mls", "")) or mls
            try:
                when = datetime.strptime(page_text(cells.get("date", "")), "%b %d, %Y").date().isoformat()
            except ValueError:
                continue  # masked
            rows.append((mls, when, page_text(cells.get("event", "")).lower(), money(cells.get("price"))))
        own = [r for r in rows if r[0] == result.get("mls_number")]
        prices = sorted((when, price) for _, when, event, price in own if price and event != "sold")
        if prices:
            result["price_history"] = prices
            result.setdefault("list_date", prices[0][0])
        sold = sorted((when, price) for m, when, event, price in rows
                      if price and event == "sold" and m != result.get("mls_number"))
        if sold:
            result["past_sales"] = sold

    @staticmethod
    def _first(facts: dict, *labels: str) -> Optional[str]:
        for label in labels:
            if facts.get(label):
                return facts[label]
        return None

    def _rx(self, text: str, pattern: str, default=None) -> Optional[str]:
        m = re.search(pattern, text or "", re.IGNORECASE | re.S)
        return m.group(1).strip() if m else default

"""Base class for all site adapters."""

from __future__ import annotations

import re
import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)

BLOCKED_MARKERS = [
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "challenge-platform",
    "datadome",
    "_dd_s",
    "managed_challenge",
    "ray id",
]


# Free-text snapshot fields scraped with loose patterns, so they can pick up page code
TEXT_FIELDS = (
    "parking", "locker", "balcony", "exposure", "broker", "fee_includes", "property_type",
    "ensuite_laundry", "outdoor_space", "possession_date", "rent_controlled",
)

# Signs that a scraped "value" is really a scrap of HTML, CSS or JSON
_PAGE_CODE = re.compile(
    r"""__typename | _svg__ | \bclass= | \bhref= | https?:// | \.css\b | \.js\b
      | [<>{}] | =" | "\s*[:,\]] | ^["',:;)\]]
      | \b(?:sm|md|lg|xl): | \btext-(?:right|left|center|\[) | \bleading-\[
      | \b(?:pl|pr|pt|pb|px|py|w|h)-\d""",
    re.VERBOSE,
)


def clean_text(value):
    """A scraped text value, or None when it is page code rather than listing data."""
    if not isinstance(value, str):
        return value
    value = value.strip()
    if not value:
        return None
    if _PAGE_CODE.search(value):
        return None
    if len(value) > 3 and value.count(":") > 2:
        return None
    return value


def clean_parsed(parsed: dict) -> dict:
    """Drop page-code scraps from the free-text fields of an adapter's parse result."""
    for name in TEXT_FIELDS:
        if name in parsed:
            parsed[name] = clean_text(parsed[name])
    return parsed


class SiteAdapter(ABC):
    """Base class for site-specific scrapers."""

    site_name: str = ""
    domain_pattern: str = ""

    @classmethod
    def matches(cls, url: str) -> bool:
        return bool(re.search(cls.domain_pattern, url, re.IGNORECASE))

    @classmethod
    def is_listing_url(cls, url: str) -> bool:
        """Return True if the URL is an individual listing (not a building/index page)."""
        return True

    @abstractmethod
    def parse(self, html: str, url: str = "") -> dict:
        """Parse HTML into a dict of snapshot fields.

        Must return at least: status, list_price, address, city.
        Any field that can't be parsed should be None with the field name
        added to parse_errors.  ``url`` is the listing URL being fetched.
        """
        ...

    def detect_blocked(self, html: str) -> bool:
        if len(html) < 2048:
            lower = html.lower()
            for marker in BLOCKED_MARKERS:
                if marker in lower:
                    return True
            return True  # sub-2KB body on a page that should be large
        lower = html[:5000].lower()
        for marker in BLOCKED_MARKERS:
            if marker in lower:
                return True
        return False

    def _safe_int(self, text: Optional[str]) -> Optional[int]:
        if not text:
            return None
        cleaned = re.sub(r"[^\d]", "", text.strip())
        return int(cleaned) if cleaned else None

    def _safe_float(self, text: Optional[str]) -> Optional[float]:
        if not text:
            return None
        cleaned = re.sub(r"[^\d.]", "", text.strip())
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None

    def _safe_price(self, text: Optional[str]) -> Optional[float]:
        if not text:
            return None
        cleaned = re.sub(r"[^\d.]", "", text.strip())
        try:
            val = float(cleaned) if cleaned else None
            return val if val and val > 0 else None
        except ValueError:
            return None

    def _rx(self, html: str, pattern: str, default=None) -> Optional[str]:
        m = re.search(pattern, html, re.IGNORECASE)
        return m.group(1).strip() if m else default

    @staticmethod
    def _sanitize_field(value: Optional[str]) -> Optional[str]:
        """Reject scraped values that contain HTML/CSS/JSON artifacts."""
        if not value:
            return value
        return clean_text(value)

    def _extract_sqft(self, html: str) -> tuple[Optional[int], Optional[int]]:
        m = re.search(r'(\d[\d,]*)\s*[-–]\s*(\d[\d,]*)\s*(?:sq\.?\s*ft|sqft|sf)', html, re.IGNORECASE)
        if m:
            return self._safe_int(m.group(1)), self._safe_int(m.group(2))
        m = re.search(r'(\d[\d,]*)\s*(?:sq\.?\s*ft|sqft|sf)', html, re.IGNORECASE)
        if m:
            v = self._safe_int(m.group(1))
            return v, v
        return None, None

    @staticmethod
    def _detect_power_of_sale(html: str) -> bool:
        """Detect power of sale / distressed sale indicators in listing HTML."""
        text = html.lower()
        patterns = [
            r'\bpower\s+of\s+sale\b',
            r'\bbank\s+sale\b',
            r'\bbank[- ]owned\b',
            r'\bjudicial\s+sale\b',
            r'\bcourt[- ]ordered\s+sale\b',
            r'\bsold\s+"?as\s+is"?\s+as\s+per\s+schedule\s+b\b',
            r'\bmortgagee\s+sale\b',
            r'\bforeclosure\b',
        ]
        return any(re.search(p, text) for p in patterns)

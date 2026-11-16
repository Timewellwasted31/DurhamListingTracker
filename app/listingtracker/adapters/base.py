"""Base class for all site adapters."""

from __future__ import annotations

import json
import re
import logging
from abc import ABC, abstractmethod
from html import unescape as html_unescape
from typing import Optional
from urllib.parse import urljoin, urlparse

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

    def extract_images(self, html: str, url: str = "") -> list[str]:
        """Extract listing photo URLs from HTML. Returns deduplicated list, best first."""
        seen: set[str] = set()
        seen_base: set[str] = set()
        result: list[str] = []

        def _base_key(img_url: str) -> str:
            """Deduplicate different sizes of the same image."""
            p = urlparse(img_url).path.lower()
            p = re.sub(r'-(?:\d{2,4}|large|medium|small|thumb|[lmst])\.(jpe?g|png|webp)', r'.\1', p)
            p = re.sub(r'\?.*$', '', p)
            return p

        def _add(img_url: str) -> None:
            if not img_url or len(img_url) > 2000:
                return
            img_url = html_unescape(img_url.strip())
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            elif img_url.startswith("/"):
                img_url = urljoin(url, img_url) if url else ""
            if not img_url.startswith("http"):
                return
            parsed = urlparse(img_url)
            path_lower = parsed.path.lower()
            if any(x in path_lower for x in ("/logo", "/icon", "/favicon", "/avatar",
                                              "/badge", "/sprite", "/pixel", "/blank",
                                              "/static/", "/agent", "/chat-bubble",
                                              ".svg", ".gif")):
                return
            bk = _base_key(img_url)
            if img_url not in seen and bk not in seen_base:
                seen.add(img_url)
                seen_base.add(bk)
                result.append(img_url)

        # 1. og:image meta tags (usually the hero image)
        for m in re.finditer(
            r'<meta\s+(?:property|name)="og:image(?::url)?"\s+content="([^"]+)"', html
        ):
            _add(m.group(1))

        # 2. JSON-LD image fields
        for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                ld = json.loads(m.group(1))
                if isinstance(ld, list):
                    for item in ld:
                        self._extract_ld_images(item, _add)
                elif isinstance(ld, dict):
                    self._extract_ld_images(ld, _add)
            except (json.JSONDecodeError, ValueError):
                pass

        # 3. High-res img tags with src matching CDN/image patterns
        for m in re.finditer(
            r'<img\b[^>]*?\bsrc="([^"]+)"[^>]*/?>',
            html,
        ):
            src = m.group(1)
            tag = m.group(0)
            if self._is_listing_photo(src, tag):
                _add(src)

        # 4. data-src / srcset attributes (lazy-loaded images)
        for m in re.finditer(r'<img\b[^>]*?\bdata-src="([^"]+)"', html):
            src = m.group(1)
            if self._is_listing_photo(src, m.group(0)):
                _add(src)

        for m in re.finditer(r'<img\b[^>]*?\bsrcset="([^"]+)"', html):
            for entry in m.group(1).split(","):
                src = entry.strip().split()[0]
                if self._is_listing_photo(src, ""):
                    _add(src)

        return result

    @staticmethod
    def _extract_ld_images(ld: dict, add_fn) -> None:
        imgs = ld.get("image")
        if isinstance(imgs, str):
            add_fn(imgs)
        elif isinstance(imgs, list):
            for img in imgs:
                if isinstance(img, str):
                    add_fn(img)
                elif isinstance(img, dict):
                    add_fn(img.get("url", "") or img.get("contentUrl", ""))

    @staticmethod
    def _is_listing_photo(src: str, tag_html: str) -> bool:
        """Heuristic: is this img src likely a listing photo (not UI chrome)?"""
        if not src or src.startswith("data:"):
            return False
        lower = src.lower()
        if any(x in lower for x in ("/logo", "/icon", "/favicon", "/avatar",
                                     "/badge", "/sprite", "/static/", "/agent",
                                     "/chat-bubble", ".svg", ".gif",
                                     "pixel", "blank", "placeholder",
                                     "google", "facebook", "twitter")):
            return False
        has_large_dim = bool(re.search(r'(?:width|w)[=_/](?:[4-9]\d{2}|\d{4,})', lower))
        has_photo_keyword = bool(re.search(
            r'(?:photo|image|gallery|listing|property|unit|media|upload|cdn|res\.cloudinary|imgix)',
            lower,
        ))
        small = re.search(r'(?:width|height)="(\d+)"', tag_html)
        if small and int(small.group(1)) < 100:
            return False
        return has_large_dim or has_photo_keyword

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

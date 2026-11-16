"""Nearby amenities via OpenStreetMap Overpass API (no API key required)."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from listingtracker import geo

logger = logging.getLogger(__name__)

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]
_MAX_BBOX_SPAN = 0.6  # degrees; ~65 km — keeps queries fast


@dataclass
class AmenityCategory:
    key: str
    label: str
    color: str
    glyph: str
    overpass_filters: list[str]
    radius: int = 2000


CATEGORIES: dict[str, AmenityCategory] = {
    "schools": AmenityCategory(
        "schools", "Schools", "#FFA726", "S",
        ['["amenity"="school"]'], 2000,
    ),
    "grocery": AmenityCategory(
        "grocery", "Grocery", "#66BB6A", "G",
        ['["shop"~"supermarket|grocery"]'], 2000,
    ),
    "transit": AmenityCategory(
        "transit", "Transit", "#42A5F5", "T",
        ['["railway"="station"]', '["amenity"="bus_station"]',
         '["public_transport"="station"]'], 3000,
    ),
    "parks": AmenityCategory(
        "parks", "Parks", "#4CAF50", "P",
        ['["leisure"="park"]'], 2000,
    ),
    "healthcare": AmenityCategory(
        "healthcare", "Healthcare", "#EF5350", "+",
        ['["amenity"~"hospital|clinic|pharmacy"]'], 3000,
    ),
}

DEFAULT_LAYERS = ["schools", "grocery", "transit"]


@dataclass
class Amenity:
    name: str
    category: str
    lat: float
    lon: float
    amenity_type: str = ""


def _overpass_post(query: str) -> list[dict]:
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_err: Exception | None = None
    for url in OVERPASS_URLS:
        req = urllib.request.Request(
            url, data=data,
            headers={
                "User-Agent": geo.USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60, context=geo._ssl_context()) as resp:
                result = json.loads(resp.read())
            return result.get("elements", [])
        except Exception as e:
            last_err = e
            logger.debug("Overpass %s failed, trying next: %s", url, e)
    raise last_err  # type: ignore[misc]


def _elements_to_amenities(elements: list[dict], category: str) -> list[Amenity]:
    amenities: list[Amenity] = []
    seen: set[str] = set()
    for el in elements:
        if el.get("type") == "way":
            c = el.get("center", {})
            lat, lon = c.get("lat"), c.get("lon")
        else:
            lat, lon = el.get("lat"), el.get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        name = tags.get("name", "")
        key = f"{name}:{lat:.4f},{lon:.4f}"
        if key in seen:
            continue
        seen.add(key)
        atype = (
            tags.get("amenity") or tags.get("shop") or tags.get("leisure")
            or tags.get("railway") or tags.get("highway")
            or tags.get("public_transport") or ""
        )
        amenities.append(Amenity(
            name=name, category=category,
            lat=lat, lon=lon, amenity_type=atype,
        ))
    return amenities


def query_bbox(category_key: str, south: float, west: float,
               north: float, east: float) -> list[Amenity]:
    """Query amenities of a category within a bounding box. Results are cached."""
    cat = CATEGORIES.get(category_key)
    if not cat:
        return []

    cache_key = f"amenity:{category_key}:{south:.3f},{west:.3f},{north:.3f},{east:.3f}"
    cached = geo._cache_get(cache_key)
    if cached is not None:
        return [Amenity(**a) for a in cached]

    lat_span = north - south
    lon_span = east - west
    if lat_span > _MAX_BBOX_SPAN or lon_span > _MAX_BBOX_SPAN:
        mid_lat = (south + north) / 2
        mid_lon = (west + east) / 2
        half = _MAX_BBOX_SPAN / 2
        south, north = mid_lat - half, mid_lat + half
        west, east = mid_lon - half, mid_lon + half

    bbox = f"{south},{west},{north},{east}"
    parts: list[str] = []
    for filt in cat.overpass_filters:
        parts.append(f"  node{filt}({bbox});")
        parts.append(f"  way{filt}({bbox});")
    query = "[out:json][timeout:45];\n(\n" + "\n".join(parts) + "\n);\nout center tags;\n"

    try:
        elements = _overpass_post(query)
    except Exception as e:
        logger.warning("Overpass query failed for %s: %s", category_key, e)
        return []

    result = _elements_to_amenities(elements, category_key)
    if len(result) > 500:
        result = result[:500]

    geo._cache_put(cache_key, [
        {"name": a.name, "category": a.category, "lat": a.lat,
         "lon": a.lon, "amenity_type": a.amenity_type}
        for a in result
    ])
    return result

"""Place autocomplete, geocoding and driving routes via free OpenStreetMap services (no API key)."""

from __future__ import annotations

import http.client
import json
import logging
import math
import socket
import sqlite3
import ssl
import threading
import time
import urllib.parse
from contextlib import closing
from dataclasses import dataclass, asdict, field
from typing import Optional

from listingtracker import paths
from listingtracker.__version__ import __version__

logger = logging.getLogger(__name__)

USER_AGENT = f"DurhamListingTracker/{__version__} (desktop app)"
PHOTON_URL = "https://photon.komoot.io"
OSRM_URL = "https://router.project-osrm.org"
TILE_HOST = "tile.openstreetmap.org"
TILE_MAX_AGE = 30 * 24 * 3600
HTTP_TIMEOUT = 10

# Ontario and its immediate neighbours: (min_lon, min_lat, max_lon, max_lat)
SEARCH_BBOX = (-95.2, 41.6, -74.3, 56.9)
DEFAULT_CENTER = (43.90, -78.90)  # Durham Region

CITY_PLACE_TYPES = {"city", "town", "village", "hamlet", "suburb", "municipality", "borough"}

Point = tuple[float, float]  # (lat, lon)


@dataclass
class Place:
    label: str
    lat: float
    lon: float
    name: str = ""
    city: str = ""

    def point(self) -> Point:
        return (self.lat, self.lon)


@dataclass
class RouteInfo:
    distance_km: float
    duration_min: float
    points: list[Point] = field(default_factory=list)
    estimated: bool = False  # True when routing was unavailable and this is a straight-line guess


@dataclass
class CommuteSummary:
    listing: Optional[Place] = None
    approximate: bool = False  # listing located by city only, not street address
    to_work: Optional[RouteInfo] = None
    from_home: Optional[RouteInfo] = None
    current: Optional[RouteInfo] = None  # today's commute: home -> work


# --- HTTP + cache ---

_ssl_ctx: Optional[ssl.SSLContext] = None


def _ssl_context() -> ssl.SSLContext:
    # The Windows certificate store fails to validate the routing servers' chain; certifi's bundle does.
    global _ssl_ctx
    if _ssl_ctx is None:
        try:
            import certifi
            _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _ssl_ctx = ssl.create_default_context()
    return _ssl_ctx


def _connect_ipv4_first(host: str, port: int, timeout: float) -> socket.socket:
    # A PC with a dead IPv6 route stalls ~20 s on each dual-stack connect before falling back,
    # so try the IPv4 addresses first.
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    infos.sort(key=lambda info: info[0] != socket.AF_INET)
    last_error: Optional[OSError] = None
    for family, socktype, proto, _canon, address in infos:
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(address)
            return sock
        except OSError as e:
            last_error = e
            sock.close()
    raise last_error or OSError(f"No address found for {host}")


class _Connection(http.client.HTTPSConnection):
    def connect(self):
        sock = _connect_ipv4_first(self.host, self.port, self.timeout)
        self.sock = _ssl_context().wrap_socket(sock, server_hostname=self.host)


def _request(conn: _Connection, path: str) -> bytes:
    conn.request("GET", path, headers={"User-Agent": USER_AGENT})
    resp = conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        raise OSError(f"HTTP {resp.status} from {conn.host}")
    return body


def http_get(url: str) -> bytes:
    parts = urllib.parse.urlsplit(url)
    conn = _Connection(parts.hostname, timeout=HTTP_TIMEOUT)
    try:
        return _request(conn, parts.path + (f"?{parts.query}" if parts.query else ""))
    finally:
        conn.close()


def _get_json(url: str) -> dict:
    return json.loads(http_get(url).decode("utf-8"))


# --- Map tiles ---

_tile_local = threading.local()


def _download_tile(path: str) -> bytes:
    # One keep-alive connection per worker thread; retry once if the server dropped it
    for attempt in (1, 2):
        conn = getattr(_tile_local, "conn", None)
        if conn is None:
            conn = _tile_local.conn = _Connection(TILE_HOST, timeout=HTTP_TIMEOUT)
        try:
            return _request(conn, path)
        except (OSError, http.client.HTTPException):
            conn.close()
            _tile_local.conn = None
            if attempt == 2:
                raise
    raise OSError("unreachable")


def fetch_tile(z: int, x: int, y: int) -> bytes:
    """PNG bytes for an OpenStreetMap tile, from the disk cache when fresh. Blocking."""
    cached = paths.TILE_CACHE_DIR / str(z) / str(x) / f"{y}.png"
    try:
        if time.time() - cached.stat().st_mtime < TILE_MAX_AGE:
            return cached.read_bytes()
    except OSError:
        pass
    try:
        data = _download_tile(f"/{z}/{x}/{y}.png")
    except (OSError, http.client.HTTPException):
        if cached.exists():
            return cached.read_bytes()  # stale beats blank when offline
        raise
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    except OSError as e:
        logger.debug("tile cache write failed: %s", e)
    return data


def _cache_connect() -> sqlite3.Connection:
    paths.GEO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(paths.GEO_CACHE_PATH, timeout=5)
    con.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return con


def _cache_get(key: str):
    try:
        with closing(_cache_connect()) as con:
            row = con.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None
    except Exception as e:
        logger.debug("geo cache read failed: %s", e)
        return None


def _cache_put(key: str, value) -> None:
    try:
        with closing(_cache_connect()) as con:
            con.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))
            con.commit()
    except Exception as e:
        logger.debug("geo cache write failed: %s", e)


# --- Photon (autocomplete / geocoding) ---

def _address_label(p: dict) -> str:
    name = p.get("name") or ""
    street = p.get("street") or ""
    number = p.get("housenumber") or ""
    city = p.get("city") or ""
    state = p.get("state") or ""

    street_line = f"{number} {street}".strip()
    parts = []
    if name and name != street:
        parts.append(name)
    if street_line:
        parts.append(street_line)
    if city and city not in parts:
        parts.append(city)
    if state and state not in parts:
        parts.append(state)
    return ", ".join(parts)


def _place_from_feature(feature: dict, kind: str) -> Optional[Place]:
    p = feature.get("properties") or {}
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        return None
    lon, lat = float(coords[0]), float(coords[1])
    name = p.get("name") or ""

    if kind == "city":
        if p.get("osm_key") != "place" or p.get("osm_value") not in CITY_PLACE_TYPES:
            return None
        if not name or p.get("state") != "Ontario":
            return None
        context = [c for c in (p.get("city"), p.get("county")) if c and c != name]
        label = f"{name} ({', '.join(context)})" if context else name
        return Place(label=label, lat=lat, lon=lon, name=name, city=name)

    label = _address_label(p)
    if not label:
        return None
    is_place = p.get("osm_key") == "place" and p.get("osm_value") in CITY_PLACE_TYPES
    city = p.get("city") or (name if is_place else "")
    return Place(label=label, lat=lat, lon=lon, name=name, city=city)


def search_places(query: str, kind: str = "address", near: Optional[Point] = None,
                  limit: int = 6) -> list[Place]:
    """Suggestions for a partially typed address (kind="address") or Ontario city (kind="city")."""
    q = query.strip()
    if len(q) < 2:
        return []
    lat, lon = near or DEFAULT_CENTER
    params = [
        ("q", q),
        ("limit", 15 if kind == "city" else limit),
        ("lat", lat),
        ("lon", lon),
        ("bbox", ",".join(str(v) for v in SEARCH_BBOX)),
    ]
    if kind == "city":
        params.append(("osm_tag", "place"))
    data = _get_json(f"{PHOTON_URL}/api/?{urllib.parse.urlencode(params)}")

    places: list[Place] = []
    seen: set[str] = set()
    for feature in data.get("features", []):
        place = _place_from_feature(feature, kind)
        if place and place.label not in seen:
            seen.add(place.label)
            places.append(place)
        if len(places) >= limit:
            break
    return places


def reverse_place(lat: float, lon: float) -> Optional[Place]:
    """Nearest known address to a point; the returned Place keeps the requested coordinates."""
    params = urllib.parse.urlencode([("lat", lat), ("lon", lon), ("limit", 1)])
    data = _get_json(f"{PHOTON_URL}/reverse?{params}")
    for feature in data.get("features", []):
        place = _place_from_feature(feature, "address")
        if place:
            place.lat, place.lon = lat, lon
            return place
    return None


def locate_city(name: str) -> Optional[Point]:
    """Coordinates of an Ontario city by name (cached)."""
    key = f"city:{name.strip().lower()}"
    cached = _cache_get(key)
    if cached:
        return (cached[0], cached[1])
    try:
        places = search_places(name, kind="city", limit=5)
    except Exception as e:
        logger.info("City lookup failed for %s: %s", name, e)
        return None
    wanted = name.strip().lower()
    match = next((p for p in places if p.name.lower() == wanted), places[0] if places else None)
    if not match:
        return None
    _cache_put(key, [match.lat, match.lon])
    return match.point()


def locate_cities(names: list[str]) -> dict[str, Point]:
    found = {}
    for name in names:
        point = locate_city(name)
        if point:
            found[name] = point
    return found


def geocode_listing(address: str, city: str) -> tuple[Optional[Place], bool]:
    """Locate a listing. Returns (place, approximate); approximate means only the city was found."""
    address = (address or "").strip()
    city = (city or "").strip()
    if not address and not city:
        return None, False

    key = f"listing:{address.lower()}|{city.lower()}"
    cached = _cache_get(key)
    if cached:
        return Place(**cached["place"]), cached["approximate"]

    place: Optional[Place] = None
    approximate = False
    if address:
        query = ", ".join(part for part in (address, city, "Ontario") if part)
        candidates = search_places(query, kind="address", limit=5)
        if city:
            place = next((c for c in candidates if c.city.lower() == city.lower()), None)
        if place is None and candidates:
            place = candidates[0]
    if place is None and city:
        point = locate_city(city)
        if point:
            place = Place(label=city.title(), lat=point[0], lon=point[1], name=city.title(), city=city.title())
            approximate = True

    if place:
        _cache_put(key, {"place": asdict(place), "approximate": approximate})
    return place, approximate


# --- Routing ---

def haversine_km(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _straight_line_estimate(a: Point, b: Point) -> RouteInfo:
    # Roads run ~30% longer than the crow flies; 50 km/h is a typical suburban door-to-door average.
    km = haversine_km(a, b) * 1.3
    return RouteInfo(distance_km=km, duration_min=km / 50 * 60, points=[a, b], estimated=True)


def driving_route(a: Point, b: Point) -> RouteInfo:
    """Driving distance/time between two points (cached). Falls back to a straight-line estimate."""
    key = f"route:{a[0]:.5f},{a[1]:.5f};{b[0]:.5f},{b[1]:.5f}"
    cached = _cache_get(key)
    if cached:
        return RouteInfo(
            distance_km=cached["distance_km"], duration_min=cached["duration_min"],
            points=[tuple(pt) for pt in cached["points"]],
        )

    url = (f"{OSRM_URL}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}"
           "?overview=simplified&geometries=geojson")
    try:
        data = _get_json(url)
        routes = data.get("routes") or []
        if data.get("code") == "Ok" and routes:
            r = routes[0]
            info = RouteInfo(
                distance_km=r["distance"] / 1000,
                duration_min=r["duration"] / 60,
                points=[(lat, lon) for lon, lat in r["geometry"]["coordinates"]],
            )
            _cache_put(key, {"distance_km": info.distance_km, "duration_min": info.duration_min,
                             "points": info.points})
            return info
    except Exception as e:
        logger.info("Routing failed: %s", e)
    return _straight_line_estimate(a, b)


def commute_summary(address: str, city: str, home: Optional[Point],
                    work: Optional[Point]) -> CommuteSummary:
    """Everything the Commute tab shows for one listing. Blocking — call off the UI thread."""
    summary = CommuteSummary()
    try:
        summary.listing, summary.approximate = geocode_listing(address, city)
    except Exception as e:
        logger.info("Geocoding failed for %s, %s: %s", address, city, e)
    if home and work:
        summary.current = driving_route(home, work)
    if summary.listing:
        here = summary.listing.point()
        if work:
            summary.to_work = driving_route(here, work)
        if home:
            summary.from_home = driving_route(home, here)
    return summary


# --- Formatting ---

def format_duration(minutes: float) -> str:
    total = max(1, round(minutes))
    if total < 60:
        return f"{total} min"
    return f"{total // 60} h {total % 60:02d} min"


def format_route(route: RouteInfo) -> str:
    text = f"{route.distance_km:.1f} km · {format_duration(route.duration_min)}"
    return f"~{text} (straight-line estimate)" if route.estimated else text


def commute_delta(new: RouteInfo, current: RouteInfo) -> tuple[str, str]:
    """Compare a new commute to today's. Returns (text, "better" | "worse" | "same")."""
    diff = round(new.duration_min) - round(current.duration_min)
    if abs(diff) < 2:
        return "about the same as your current commute", "same"
    if diff < 0:
        return f"{format_duration(-diff)} shorter than your current commute", "better"
    return f"{format_duration(diff)} longer than your current commute", "worse"

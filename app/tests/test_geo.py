"""Tests for geocoding, routing and commute logic (network calls mocked)."""

import os
from unittest.mock import patch

import pytest

from listingtracker import geo
from listingtracker.ui.map_widget import project, unproject


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path):
    with patch("listingtracker.paths.GEO_CACHE_PATH", tmp_path / "geo_cache.db"):
        yield


def _feature(lon, lat, **props):
    return {"geometry": {"coordinates": [lon, lat]}, "properties": props}


OSHAWA = _feature(-78.8635, 43.8976, name="Oshawa", county="Durham", state="Ontario",
                  osm_key="place", osm_value="city")
BOWMANVILLE = _feature(-78.6892, 43.9123, name="Bowmanville", city="Clarington", county="Durham",
                       state="Ontario", osm_key="place", osm_value="town")
OSHKOSH = _feature(-88.54, 44.02, name="Oshkosh", state="Wisconsin", osm_key="place", osm_value="city")
CIVIC_SQUARE = _feature(-78.8655, 43.8959, name="Oshawa Civic Square", city="Oshawa", state="Ontario",
                        osm_key="place", osm_value="square")
DOOLEY = _feature(-79.0311, 43.8828, housenumber="157", street="Dooley Crescent", city="Ajax",
                  state="Ontario", osm_key="place", osm_value="house_number")


def _osrm(distance_m, duration_s, coords=((-79.0, 43.85), (-78.9, 43.9))):
    return {"code": "Ok", "routes": [{"distance": distance_m, "duration": duration_s,
                                      "geometry": {"coordinates": [list(c) for c in coords]}}]}


def test_city_search_keeps_only_ontario_towns():
    with patch("listingtracker.geo._get_json", return_value={"features": [OSHAWA, CIVIC_SQUARE, OSHKOSH, BOWMANVILLE]}):
        places = geo.search_places("osh", kind="city")
    assert [p.name for p in places] == ["Oshawa", "Bowmanville"]
    assert places[0].label == "Oshawa (Durham)"
    assert places[1].label == "Bowmanville (Clarington, Durham)"


def test_address_search_label_and_city():
    with patch("listingtracker.geo._get_json", return_value={"features": [DOOLEY, DOOLEY]}):
        places = geo.search_places("157 dooley")
    assert len(places) == 1  # duplicates collapsed
    assert places[0].label == "157 Dooley Crescent, Ajax, Ontario"
    assert places[0].city == "Ajax"
    assert places[0].point() == (43.8828, -79.0311)


def test_short_query_makes_no_request():
    with patch("listingtracker.geo._get_json") as get:
        assert geo.search_places("a") == []
    get.assert_not_called()


def test_reverse_keeps_clicked_coordinates():
    with patch("listingtracker.geo._get_json", return_value={"features": [DOOLEY]}):
        place = geo.reverse_place(43.9, -79.0)
    assert place.city == "Ajax"
    assert (place.lat, place.lon) == (43.9, -79.0)


def test_geocode_listing_prefers_matching_city_and_caches():
    other = _feature(-79.5, 43.7, housenumber="157", street="Dooley Crescent", city="Toronto", state="Ontario")
    with patch("listingtracker.geo._get_json", return_value={"features": [other, DOOLEY]}) as get:
        place, approximate = geo.geocode_listing("157 Dooley Cres", "ajax")
        again, _ = geo.geocode_listing("157 Dooley Cres", "ajax")
    assert place.city == "Ajax" and not approximate
    assert again == place
    assert get.call_count == 1


def test_geocode_listing_falls_back_to_city_centre():
    def fake(url):
        return {"features": [OSHAWA]} if "osm_tag=place" in url else {"features": []}

    with patch("listingtracker.geo._get_json", side_effect=fake):
        place, approximate = geo.geocode_listing("999 Nowhere Rd", "Oshawa")
    assert approximate
    assert place.point() == (43.8976, -78.8635)


def test_driving_route_parses_and_caches():
    with patch("listingtracker.geo._get_json", return_value=_osrm(18_200, 1_260)) as get:
        route = geo.driving_route((43.85, -79.0), (43.9, -78.9))
        cached = geo.driving_route((43.85, -79.0), (43.9, -78.9))
    assert route.distance_km == pytest.approx(18.2)
    assert route.duration_min == pytest.approx(21)
    assert route.points == [(43.85, -79.0), (43.9, -78.9)]  # flipped to (lat, lon)
    assert not route.estimated
    assert cached.distance_km == route.distance_km
    assert get.call_count == 1


def test_driving_route_estimates_when_routing_is_down():
    with patch("listingtracker.geo._get_json", side_effect=OSError("offline")):
        fallback = geo.driving_route((43.0, -79.0), (44.0, -79.0))
    assert fallback.estimated
    assert fallback.distance_km == pytest.approx(111.2 * 1.3, rel=0.01)
    # an estimate must never be cached as if it were a real route
    with patch("listingtracker.geo._get_json", return_value=_osrm(130_000, 5_400)):
        assert not geo.driving_route((43.0, -79.0), (44.0, -79.0)).estimated


def test_fetch_tile_downloads_once_then_reads_disk(tmp_path):
    with (patch("listingtracker.paths.TILE_CACHE_DIR", tmp_path),
          patch("listingtracker.geo._download_tile", return_value=b"PNGDATA") as download):
        assert geo.fetch_tile(11, 573, 745) == b"PNGDATA"
        assert geo.fetch_tile(11, 573, 745) == b"PNGDATA"
    download.assert_called_once_with("/11/573/745.png")
    assert (tmp_path / "11" / "573" / "745.png").read_bytes() == b"PNGDATA"


def test_fetch_tile_serves_stale_copy_when_offline(tmp_path):
    stale = tmp_path / "11" / "573" / "745.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"OLD")
    os.utime(stale, (0, 0))  # 1970: far past the freshness window
    with (patch("listingtracker.paths.TILE_CACHE_DIR", tmp_path),
          patch("listingtracker.geo._download_tile", side_effect=OSError("offline"))):
        assert geo.fetch_tile(11, 573, 745) == b"OLD"
        with pytest.raises(OSError):
            geo.fetch_tile(11, 1, 1)


def test_commute_summary_routes_from_listing():
    def fake(url):
        if "/route/" in url:
            return _osrm(20_000, 1_500)
        return {"features": [DOOLEY]}

    with patch("listingtracker.geo._get_json", side_effect=fake):
        summary = geo.commute_summary("157 Dooley Cres", "Ajax", home=(43.9, -78.86), work=(43.65, -79.38))
    assert summary.listing.city == "Ajax"
    assert summary.to_work and summary.from_home and summary.current


def test_commute_summary_without_home_or_listing():
    with patch("listingtracker.geo._get_json", return_value={"features": []}):
        summary = geo.commute_summary("", "", home=None, work=(43.65, -79.38))
    assert summary.listing is None
    assert summary.to_work is None and summary.current is None


def test_formatting_and_delta():
    assert geo.format_duration(21.4) == "21 min"
    assert geo.format_duration(72) == "1 h 12 min"
    assert geo.format_route(geo.RouteInfo(18.24, 21)) == "18.2 km · 21 min"
    assert geo.format_route(geo.RouteInfo(18.24, 21, estimated=True)).startswith("~18.2 km")

    current = geo.RouteInfo(30, 40)
    assert geo.commute_delta(geo.RouteInfo(20, 31), current) == ("9 min shorter than your current commute", "better")
    assert geo.commute_delta(geo.RouteInfo(40, 55), current)[1] == "worse"
    assert geo.commute_delta(geo.RouteInfo(30, 41), current)[1] == "same"


def test_haversine_known_distance():
    # Oshawa to Toronto city hall is roughly 50 km in a straight line
    assert geo.haversine_km((43.8971, -78.8658), (43.6535, -79.3839)) == pytest.approx(49.6, abs=1.5)


def test_map_projection_round_trip():
    for lat, lon in [(43.9, -78.9), (0.0, 0.0), (-33.9, 151.2)]:
        for zoom in (4, 11, 18):
            back = unproject(*project(lat, lon, zoom), zoom)
            assert back == pytest.approx((lat, lon), abs=1e-6)
    # zoom 0: the whole world is one 256px tile centred on (0, 0)
    assert project(0, 0, 0) == pytest.approx((128, 128))

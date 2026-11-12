"""Tests for the profile system."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from listingtracker.profiles import (
    BuyerProfile, save_profile, load_profile, list_profiles, delete_profile,
    _dict_to_profile, profile_exists,
)


def test_profile_defaults():
    p = BuyerProfile()
    assert p.name == "Default"
    assert p.annual_income == 60_000
    assert p.price_ceiling == 450_000


def test_profile_to_finance_dict():
    p = BuyerProfile(annual_income=80_000, contract_rate=0.05)
    d = p.to_finance_dict()
    assert d["annual_income"] == 80_000
    assert d["contract_rate"] == 0.05


def test_buffered_ceilings():
    p = BuyerProfile(
        price_ceiling=450_000,
        monthly_allin_ceiling=2_100,
        fee_per_sqft_reject=1.00,
        price_buffer_pct=10.0,
        monthly_buffer_pct=10.0,
        fee_buffer_pct=15.0,
    )
    assert abs(p.buffered_price_ceiling() - 495_000) < 1
    assert abs(p.buffered_monthly_ceiling() - 2_310) < 1
    assert abs(p.buffered_fee_reject() - 1.15) < 0.001


def test_search_cities_default():
    p = BuyerProfile(cities=[])
    cities = p.search_cities()
    assert "oshawa" in cities
    assert "pickering" in cities


def test_search_cities_custom():
    p = BuyerProfile(cities=["Ajax", "Whitby"])
    assert p.search_cities() == ["Ajax", "Whitby"]


def test_save_load_profile(tmp_path):
    with patch("listingtracker.profiles.PROFILES_DIR", tmp_path):
        p = BuyerProfile(
            name="Test Profile",
            annual_income=75_000,
            cities=["Oshawa", "Ajax"],
            deal_breakers=["No parking", "No balcony"],
        )
        save_profile(p)
        loaded = load_profile("Test Profile")
        assert loaded is not None
        assert loaded.name == "Test Profile"
        assert loaded.annual_income == 75_000
        assert loaded.cities == ["Oshawa", "Ajax"]
        assert loaded.deal_breakers == ["No parking", "No balcony"]


def test_list_profiles(tmp_path):
    with patch("listingtracker.profiles.PROFILES_DIR", tmp_path):
        save_profile(BuyerProfile(name="Alpha"))
        save_profile(BuyerProfile(name="Beta"))
        names = list_profiles()
        assert "Alpha" in names
        assert "Beta" in names


def test_delete_profile(tmp_path):
    with patch("listingtracker.profiles.PROFILES_DIR", tmp_path):
        save_profile(BuyerProfile(name="Temp"))
        assert delete_profile("Temp") is True
        assert load_profile("Temp") is None


def test_dict_to_profile_partial():
    data = {"name": "Partial", "annual_income": 90_000}
    p = _dict_to_profile(data)
    assert p.name == "Partial"
    assert p.annual_income == 90_000
    assert p.price_ceiling == 450_000  # default preserved


def test_proximity_migration():
    data = {"name": "Legacy", "proximity_to": "LCBO work locations / GO station access"}
    p = _dict_to_profile(data)
    assert p.proximity_pois == ["LCBO work locations", "GO station access"]


def test_proximity_migration_skips_when_pois_exist():
    data = {"name": "New", "proximity_to": "old", "proximity_pois": ["Already set"]}
    p = _dict_to_profile(data)
    assert p.proximity_pois == ["Already set"]


def test_save_load_profile_with_special_chars(tmp_path):
    with patch("listingtracker.profiles.PROFILES_DIR", tmp_path):
        p = BuyerProfile(name="Shayne — Durham", annual_income=60_000)
        save_profile(p)
        loaded = load_profile("Shayne — Durham")
        assert loaded is not None
        assert loaded.name == "Shayne — Durham"
        assert loaded.annual_income == 60_000


def test_home_work_points_default_unset():
    p = BuyerProfile()
    assert p.home_point() is None
    assert p.work_point() is None


def test_save_load_profile_with_locations(tmp_path):
    with patch("listingtracker.profiles.PROFILES_DIR", tmp_path):
        p = BuyerProfile(
            name="Located",
            home_address="12 Main St, Oshawa, Ontario", home_lat=43.9, home_lon=-78.86,
            work_address="Toronto City Hall", work_lat=43.6535, work_lon=-79.3839,
            cities=["Whitby"], city_coords={"Whitby": [43.88, -78.94]},
            property_types=["house"],
        )
        save_profile(p)
        assert profile_exists("Located")
        assert not profile_exists("Someone Else")
        loaded = load_profile("Located")
        assert loaded.home_point() == (43.9, -78.86)
        assert loaded.work_point() == (43.6535, -79.3839)
        assert loaded.city_coords == {"Whitby": [43.88, -78.94]}
        assert loaded.property_types == ["house"]


def test_old_profile_without_locations_still_loads():
    p = _dict_to_profile({"name": "Old", "cities": ["oshawa"]})
    assert p.home_point() is None
    assert p.city_coords == {}

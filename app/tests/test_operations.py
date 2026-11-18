"""The phone starting and following the desktop's long jobs, and qualification on the phone."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from listingtracker import api
from listingtracker.finance import FinanceConfig
from listingtracker.operations import Operations


@pytest.fixture
def ops(monkeypatch):
    fresh = Operations()
    monkeypatch.setattr("listingtracker.operations.operations", fresh)
    return fresh


def test_a_job_reports_progress_then_its_result(ops):
    ops.started("recheck", total=3)
    ops.progress(2, 3, "580 Mary St E")
    s = ops.status()
    assert (s["running"], s["current"], s["total"], s["text"]) == ("recheck", 2, 3, "580 Mary St E")
    ops.finished("3 checked, 1 change")
    s = ops.status()
    assert s["running"] is None and s["started"] is None
    assert (s["last"]["job"], s["last"]["message"], s["last"]["ok"]) == ("recheck", "3 checked, 1 change", True)


def test_a_refusal_is_reported_without_a_job_running(ops):
    ops.refused("search", "Choose a buyer profile first")
    s = ops.status()
    assert s["running"] is None and s["last"]["job"] == "search" and s["last"]["ok"] is False


def test_progress_outside_a_job_is_ignored(ops):
    ops.progress(5, 10, "stray")
    assert ops.status()["text"] == ""


def test_without_the_desktop_window_jobs_cannot_start(ops):
    with pytest.raises(HTTPException) as e:
        api.start_operation("recheck")
    assert e.value.status_code == 503


def test_jobs_are_handed_to_the_desktop_window(ops):
    asked = []
    ops.attach(asked.append)
    api.start_operation("search")
    api.start_operation("cancel")
    assert asked == ["search", "cancel"]
    with pytest.raises(HTTPException) as e:
        api.start_operation("delete-everything")
    assert e.value.status_code == 404
    ops.attach(None)  # the window closed
    assert api.operations_status()["available"] is False


def snap(price, fee=500, taxes=3000, pos=False):
    return SimpleNamespace(list_price=price, maintenance_fee=fee, taxes_annual=taxes, fee_includes="",
                           power_of_sale=pos, balcony=None, parking=None, sqft_min=800, sqft_max=900)


def test_cards_say_whether_the_buyer_qualifies():
    cfg = FinanceConfig(annual_income=90_000, down_payment_available=100_000)
    cheap, dear = api._money(snap(300_000), cfg), api._money(snap(1_500_000, pos=True), cfg)
    assert cheap["qualifies"] is True and dear["qualifies"] is False
    assert dear["pos"] is True and cheap["pos"] is None
    assert 0 < cheap["monthly"] < dear["monthly"]


def test_without_an_income_qualification_is_left_unsaid():
    cfg = FinanceConfig(annual_income=0)
    assert api._money(snap(300_000), cfg)["qualifies"] is None
    assert api._buyer_summary(cfg, None) == {"can_qualify": False}
    assert api._money(snap(None), cfg) == {}


def test_affordability_lists_the_max_price_and_deal_breakers():
    cfg = FinanceConfig(annual_income=90_000, down_payment_available=100_000)
    profile = SimpleNamespace(name="Us", outdoor_space_required=False, parking_required=False,
                              price_ceiling=500_000, buffered_price_ceiling=lambda: 525_000,
                              fee_per_sqft_reject=1.0, monthly_allin_target=2500, monthly_allin_ceiling=3000)
    a = api._affordability(snap(600_000), cfg, profile)
    assert a["qualifies"] in (True, False) and a["max_price"] % 1000 == 0
    assert a["deal_breakers"] and "price ceiling" in a["deal_breakers"][0]
    assert (a["profile"], a["monthly_target"]) == ("Us", 2500)


@pytest.fixture
def profile_files(tmp_path, monkeypatch):
    from listingtracker import config, profiles
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(profiles, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    profiles.save_profile(profiles.BuyerProfile(name="Shayne — Durham", annual_income=123_000))
    return profiles


def test_without_the_desktop_the_server_switches_profiles_itself(ops, profile_files):
    from listingtracker.config import load_config
    assert api.set_profile("Shayne — Durham") == {"profiles": ["Shayne — Durham"], "active": "Shayne — Durham"}
    assert load_config()["annual_income"] == 123_000  # its finances now drive "qualifies"
    with pytest.raises(HTTPException) as e:
        api.set_profile("Nobody")
    assert e.value.status_code == 404


def test_with_the_desktop_open_the_window_switches_profiles(ops, profile_files, monkeypatch):
    monkeypatch.setattr(api, "PROFILE_SWITCH_WAIT", 0.2)
    asked = []
    ops.attach(asked.append)
    api.set_profile("Shayne — Durham")
    assert asked == ["profile:Shayne — Durham"]

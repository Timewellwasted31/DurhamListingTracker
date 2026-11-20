"""The user's saved sign-ins to listing sites: which site, what is kept, and that it stays private."""

import sys

import pytest

from listingtracker import siteaccounts as sa


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, "SESSIONS_DIR", tmp_path)
    signed_in = {"strata"}
    monkeypatch.setattr(sa, "is_signed_in", lambda k: k in signed_in)
    return signed_in


def test_pages_belong_to_their_site():
    assert sa.site_for_url("https://strata.ca/whitby/x/unit-5").key == "strata"
    assert sa.site_for_url("https://www.zolo.ca/oshawa-real-estate/1-a-st").key == "zolo"
    assert sa.site_for_url("https://notzolo.ca/x") is None
    assert sa.site_for_url("https://wahi.com/ca/en/x") is None  # closed: nothing to sign in to


STATE = {
    "cookies": [
        {"name": "session", "domain": ".strata.ca", "value": "s"},
        {"name": "token", "domain": "api.slaterealty.ca", "value": "t"},
        {"name": "SID", "domain": ".google.com", "value": "g"},   # the Google sign-in: not kept
    ],
    "origins": [{"origin": "https://strata.ca", "localStorage": [{"name": "auth", "value": "a"}]},
                {"origin": "https://accounts.google.com", "localStorage": []}],
}


def test_only_the_sites_own_sign_in_is_kept():
    kept = sa.keep_only(STATE, sa.SITES_BY_KEY["strata"])
    assert [c["name"] for c in kept["cookies"]] == ["session", "token"]
    assert [o["origin"] for o in kept["origins"]] == ["https://strata.ca"]


def test_a_saved_sign_in_is_used_only_for_signed_in_sites(sessions):
    sa.save_session("strata", STATE)
    sa.save_session("zolo", {"cookies": [{"name": "z", "domain": ".zolo.ca", "value": "z"}], "origins": []})
    state = sa.storage_state_for(["https://strata.ca/a/b", "https://www.zolo.ca/c"])
    assert [c["name"] for c in state["cookies"]] == ["session", "token"]   # zolo: not signed in
    sessions.add("zolo")
    state = sa.storage_state_for(["https://strata.ca/a/b", "https://www.zolo.ca/c"])
    assert sorted(c["name"] for c in state["cookies"]) == ["session", "token", "z"]
    assert sa.storage_state_for(["https://www.realtor.ca/x"]) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows encryption")
def test_the_saved_sign_in_is_encrypted(sessions):
    sa.save_session("strata", STATE)
    raw = (sa.SESSIONS_DIR / "strata.bin").read_bytes()
    assert b"session" not in raw and b"slaterealty" not in raw
    assert sa.load_session("strata")["cookies"][0]["value"] == "s"


@pytest.mark.parametrize("profile, signed_out, expected", [
    (True, None, True), (True, "2026-11-18T10:00", False), (False, None, False)])
def test_a_strata_sign_in_from_2_11_carries_over(tmp_path, monkeypatch, profile, signed_out, expected):
    import listingtracker.db as db
    state = {"building_sales_signed_out": signed_out}
    monkeypatch.setattr(db, "get_state", state.get)
    monkeypatch.setattr(db, "set_state", state.__setitem__)
    folder = tmp_path / "strata-opera"
    if profile:
        folder.mkdir()
    monkeypatch.setattr(sa, "profile_dir", lambda k: folder)
    assert sa.is_signed_in("strata") is expected
    assert bool(state[sa.STATE_PREFIX + "strata"]) is expected   # decided once, then remembered
    assert sa.is_signed_in("zolo") is False


def test_a_site_profile_is_used_by_one_window_at_a_time():
    with sa.claim("zolo"):
        with pytest.raises(sa.Busy):
            with sa.claim("zolo"):
                pass
        with sa.claim("condos"):
            pass
    with sa.claim("zolo"):
        pass

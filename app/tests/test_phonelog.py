"""Problems the phone reports once it reaches the computer again."""

from datetime import datetime, timedelta, timezone

import pytest

from listingtracker import phonelog


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.setattr(phonelog, "LOG_PATH", tmp_path / "phone-errors.log")
    monkeypatch.setattr(phonelog, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(phonelog, "_seen", phonelog.OrderedDict())
    monkeypatch.setattr(phonelog, "_started", 0.0)
    return tmp_path / "phone-errors.log"


def now(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def test_each_report_says_whether_the_request_reached_the_computer(log):
    phonelog.remember("ok-1", "/api/listings/5", 200, 42)
    kept = phonelog.record([
        {"id": "a", "at": now(), "kind": "timeout", "method": "GET", "path": "/api/listings/5",
         "attempt": 1, "of": 3, "ms": 15003, "request_id": "ok-1", "online": True, "network": "wifi"},
        {"id": "b", "at": now(), "kind": "network", "path": "/api/listings/5", "message": "Failed to fetch",
         "attempt": 2, "of": 3, "ms": 120, "request_id": "lost-2", "online": True, "network": "cellular",
         "rtt": 300},
    ], agent="Android WebView")
    assert kept == 2
    lines = log.read_text(encoding="utf-8").splitlines()
    assert "2 from the phone (Android WebView)" in lines[0]
    assert "TIMEOUT | GET /api/listings/5 | try 1/3 | after 15003 ms" in lines[1]
    assert "reached the computer, which answered 200 in 42 ms" in lines[1]
    assert "'Failed to fetch'" in lines[2] and "never reached the computer" in lines[2]
    assert "phone online (cellular, rtt 300 ms)" in lines[2]


def test_reports_from_before_a_restart_are_not_called_lost(log, monkeypatch):
    monkeypatch.setattr(phonelog, "_started", datetime.now(timezone.utc).timestamp())
    phonelog.record([{"at": now(hours=-2), "kind": "network", "request_id": "x"}])
    assert "unknown: the computer's app was restarted since" in log.read_text(encoding="utf-8")


def test_reports_are_cleaned_and_capped(log):
    junk = [{"kind": "script", "message": "first line\nsecond line" + "x" * 5000, "status": "not a number",
             "unexpected": "dropped"}, "not a report", {}]
    assert phonelog.record(junk * 300) == phonelog.MAX_REPORTS // 3 + 1  # every third is the good one
    text = log.read_text(encoding="utf-8")
    assert "unexpected" not in text and "first line second line" in text and "x" * 300 not in text
    assert len(text.splitlines()) == 1 + phonelog.MAX_REPORTS // 3 + 1


def test_only_the_most_recent_request_ids_are_remembered(log, monkeypatch):
    monkeypatch.setattr(phonelog, "REMEMBERED_REQUESTS", 3)
    for i in range(5):
        phonelog.remember(f"r{i}", "/api/listings", 200, 5)
    assert list(phonelog._seen) == ["r2", "r3", "r4"]



@pytest.fixture
def connections(tmp_path, monkeypatch):
    monkeypatch.setattr(phonelog, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(phonelog, "CONNECTIONS_LOG", tmp_path / "phone-connections.log")
    monkeypatch.setattr(phonelog, "CONNECTIONS_PATH", tmp_path / "phone-connections.json")
    monkeypatch.setattr(phonelog, "_connections", None)
    monkeypatch.setattr(phonelog, "_current", None)
    monkeypatch.setattr(phonelog, "_phone_at", {})
    monkeypatch.setattr(phonelog, "_save_timer", None)
    monkeypatch.setattr(phonelog, "SAVE_AFTER", 0.05)
    return tmp_path


def test_each_change_of_phone_address_or_route_is_logged_and_every_connection_tallied(connections):
    import json
    phonelog.connection("203.0.113.7", "203.0.113.7:8443", "wifi, wifi 192.168.1.45")
    phonelog.connection("203.0.113.7", "203.0.113.7:8443", None)  # the page itself: same phone
    phonelog.connection("203.0.113.7", "203.0.113.7:8443", "wifi, wifi 192.168.1.45")
    phonelog.connection("127.0.0.1", "127.0.0.1:8000", None)  # this computer: not the phone
    phonelog.connection("198.51.100.5", "durham.duckdns.org:8443", "mobile data, mobile 10.20.30.40")
    phonelog.connection("203.0.113.7", "203.0.113.7:8443", "wifi, wifi 192.168.1.45")

    lines = (connections / "phone-connections.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3  # first connection, the move to mobile data, and back
    assert "phone wifi, wifi 192.168.1.45" in lines[0] and "from 203.0.113.7" in lines[0]
    assert "first time" in lines[0] and "via durham.duckdns.org:8443" in lines[1]
    assert "seen before: 3 requests" in lines[2] and "2 different connections so far" in lines[2]

    tally = json.loads((connections / "phone-connections.json").read_text(encoding="utf-8"))
    assert sorted(e["requests"] for e in tally.values()) == [1, 4]


def test_requests_that_change_nothing_are_saved_soon_after(connections):
    import json
    import time
    phonelog.connection("203.0.113.7", "203.0.113.7:8443", "wifi, wifi 192.168.1.45")
    for _ in range(3):
        phonelog.connection("203.0.113.7", "203.0.113.7:8443", None)
    time.sleep(0.3)
    tally = json.loads((connections / "phone-connections.json").read_text(encoding="utf-8"))
    assert [e["requests"] for e in tally.values()] == [4]

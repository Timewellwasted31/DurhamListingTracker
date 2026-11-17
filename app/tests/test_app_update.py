"""The desktop server offering the phone app's newest build."""

import hashlib
import json
import os

import pytest
from fastapi import HTTPException

from listingtracker import api


@pytest.fixture
def apk_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "APK_PATH", tmp_path / "DurhamListings.apk")
    monkeypatch.setattr(api, "APK_VERSION_PATH", tmp_path / "version.json")
    api._apk_info.cache_clear()
    return tmp_path


def publish(folder, data: bytes, code: int, name: str):
    (folder / "DurhamListings.apk").write_bytes(data)
    (folder / "version.json").write_text(json.dumps({"version_code": code, "version_name": name}))


def test_no_build_on_this_computer_is_a_404(apk_dir):
    with pytest.raises(HTTPException) as e:
        api.android_release()
    assert e.value.status_code == 404


def test_release_describes_the_apk_on_disk(apk_dir):
    publish(apk_dir, b"first build", 4, "2.9.0")
    assert api.android_release() == {
        "version_code": 4, "version_name": "2.9.0",
        "size": len(b"first build"), "sha256": hashlib.sha256(b"first build").hexdigest(),
    }


def test_a_new_build_is_picked_up_without_restarting(apk_dir):
    publish(apk_dir, b"first build", 4, "2.9.0")
    api.android_release()
    publish(apk_dir, b"second, longer build", 5, "2.9.1")
    stamp = os.stat(apk_dir / "DurhamListings.apk").st_mtime_ns + 1_000_000
    os.utime(apk_dir / "DurhamListings.apk", ns=(stamp, stamp))  # same-tick writes on fast disks
    info = api.android_release()
    assert (info["version_code"], info["sha256"]) == (5, hashlib.sha256(b"second, longer build").hexdigest())


def test_an_apk_without_its_version_file_is_not_offered(apk_dir):
    (apk_dir / "DurhamListings.apk").write_bytes(b"orphan")
    with pytest.raises(HTTPException):
        api.android_release()

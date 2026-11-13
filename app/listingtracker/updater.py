"""Auto-update: check GitHub releases and install newer versions."""

from __future__ import annotations

import json
import logging
import tempfile
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable

from listingtracker.__version__ import __version__

logger = logging.getLogger(__name__)

GITHUB_OWNER = "Timewellwasted31"
GITHUB_REPO = "DurhamListingTracker"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
INSTALLER_ASSET_NAME = "DurhamListingTrackerSetup.exe"


def parse_version(tag: str) -> tuple[int, ...]:
    """Parse a version tag like 'v2.1.0' or '2.1.0' into a comparable tuple."""
    cleaned = tag.lstrip("vV").strip()
    parts = []
    for segment in cleaned.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            break
    return tuple(parts) or (0,)


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    download_url: str
    release_notes: str
    is_newer: bool


def check_for_update() -> Optional[UpdateInfo]:
    """Query the GitHub API for the latest release. Returns None on network failure."""
    try:
        req = urllib.request.Request(
            RELEASES_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "DurhamListingTracker"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        logger.info("Update check failed: %s", exc)
        return None

    tag = data.get("tag_name", "")
    latest_tuple = parse_version(tag)
    current_tuple = parse_version(__version__)
    is_newer = latest_tuple > current_tuple

    download_url = ""
    for asset in data.get("assets", []):
        if asset.get("name") == INSTALLER_ASSET_NAME:
            download_url = asset.get("browser_download_url", "")
            break

    return UpdateInfo(
        current_version=__version__,
        latest_version=tag.lstrip("vV"),
        download_url=download_url,
        release_notes=data.get("body", "") or "",
        is_newer=is_newer,
    )


def download_installer(
    url: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Download the installer to a temp directory. Returns the path to the .exe."""
    dest_dir = Path(tempfile.mkdtemp(prefix="dlt_update_"))
    dest = dest_dir / INSTALLER_ASSET_NAME

    req = urllib.request.Request(url, headers={"User-Agent": "DurhamListingTracker"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total)

    return dest


def launch_installer(installer_path: Path) -> None:
    """Run the Inno Setup installer silently and exit the current process."""
    subprocess.Popen(
        [str(installer_path), "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    sys.exit(0)

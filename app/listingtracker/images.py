"""Listing image downloading and caching."""

from __future__ import annotations

import hashlib
import logging
import urllib.request
from pathlib import Path
from typing import Optional

from listingtracker.paths import IMAGE_CACHE_DIR, HTML_CACHE_DIR
from listingtracker.db import get_listing_images, update_image_local_path

logger = logging.getLogger(__name__)

MAX_IMAGES_PER_LISTING = 20
DOWNLOAD_TIMEOUT = 15


def _ssl_context():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _guess_extension(url: str, content_type: str = "") -> str:
    lower = url.lower().split("?")[0]
    if lower.endswith(".png"):
        return ".png"
    if lower.endswith(".webp"):
        return ".webp"
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    return ".jpg"


def image_dir_for_listing(listing_id: int) -> Path:
    d = IMAGE_CACHE_DIR / str(listing_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_image(url: str, dest: Path) -> bool:
    import socket
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(DOWNLOAD_TIMEOUT)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                          " (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": url.split("/", 3)[:3][0] + "//" + url.split("/")[2] + "/",
        })
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT, context=_ssl_context()) as resp:
            data = resp.read()
            if len(data) < 500:
                return False
            dest.write_bytes(data)
            return True
    except Exception as e:
        logger.debug("Failed to download %s: %s", url, e)
        return False
    finally:
        socket.setdefaulttimeout(old_timeout)


def download_listing_images(listing_id: int) -> list[str]:
    """Download all images for a listing that aren't cached yet. Returns local paths."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    rows = get_listing_images(listing_id)
    if not rows:
        return []

    dest_dir = image_dir_for_listing(listing_id)
    ordered: list[tuple[int, dict]] = list(enumerate(rows[:MAX_IMAGES_PER_LISTING]))
    results: dict[int, str] = {}

    def _download_one(idx: int, row: dict) -> tuple[int, str]:
        local = row.get("local_path")
        if local and Path(local).exists():
            return idx, local

        url = row["url"]
        ext = _guess_extension(url)
        filename = f"{_url_hash(url)}{ext}"
        dest = dest_dir / filename

        if dest.exists():
            local_str = str(dest)
            update_image_local_path(row["id"], local_str)
            return idx, local_str

        if download_image(url, dest):
            local_str = str(dest)
            update_image_local_path(row["id"], local_str)
            return idx, local_str

        return idx, ""

    overall_timeout = DOWNLOAD_TIMEOUT * 4
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_download_one, i, row): i for i, row in ordered}
        for fut in as_completed(futures, timeout=overall_timeout):
            try:
                idx, path = fut.result()
                if path:
                    results[idx] = path
            except Exception:
                pass

    return [results[i] for i in sorted(results)]


def _find_cached_html(snapshot_id: int) -> Path | None:
    """Find the cached HTML file for a snapshot ID."""
    for day_dir in sorted(HTML_CACHE_DIR.iterdir(), reverse=True):
        if not day_dir.is_dir():
            continue
        candidate = day_dir / f"{snapshot_id}.html.gz"
        if candidate.exists():
            return candidate
    return None


def extract_from_cached_html(listing_id: int, url: str, snapshot_id: int | None = None,
                              raw_html_path: str | None = None) -> list[str]:
    """Extract image URLs from an already-cached HTML file and save them to DB."""
    from listingtracker.db import save_listing_images, get_listing_images

    if get_listing_images(listing_id):
        return [r["url"] for r in get_listing_images(listing_id)]

    html_path = None
    if raw_html_path:
        p = Path(raw_html_path)
        if p.exists():
            html_path = p
    if not html_path and snapshot_id:
        html_path = _find_cached_html(snapshot_id)
    if not html_path:
        return []

    try:
        import gzip
        with gzip.open(html_path, "rt", encoding="utf-8") as f:
            html = f.read()
    except Exception:
        return []

    from listingtracker.fetcher import get_adapter_for_url
    adapter = get_adapter_for_url(url)
    if not adapter:
        return []

    try:
        image_urls = adapter.extract_images(html, url=url)
    except Exception:
        image_urls = []

    if image_urls:
        save_listing_images(listing_id, image_urls, source=adapter.site_name)
    return image_urls


def get_cached_images(listing_id: int) -> list[str]:
    """Return already-downloaded image paths for a listing (no network)."""
    rows = get_listing_images(listing_id)
    paths: list[str] = []
    for row in rows[:MAX_IMAGES_PER_LISTING]:
        local = row.get("local_path")
        if local and Path(local).exists():
            paths.append(local)
    return paths

"""Listing pages saved at each fetch, and refreshing the database from them.

Every fetch keeps its page as cache/html/<date>/<snapshot id>.html.gz. When the app learns to read
something new from a page, listings fetched before then are filled in from their newest saved page
instead of waiting to be fetched again.
"""

from __future__ import annotations

import gzip
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# Raise this whenever an adapter learns to read something new from a page. Every listing is then
# refreshed once from its newest saved page, the next time the app starts.
#   1: site price history   2: Wahi photos, earlier sales   3: area sales, Wahi and Strata home types
#   4: leases no longer counted as earlier sales
PAGE_DATA_VERSION = 4
STATE_KEY = "page_data_version"


def newest_pages() -> dict[int, tuple[str, Path]]:
    """{listing id: (listing url, its newest saved page)} for listings that have one."""
    from listingtracker.db import get_snapshot_ids
    from listingtracker.paths import HTML_CACHE_DIR

    files = {p.name.split(".")[0]: p for p in HTML_CACHE_DIR.glob("*/*.html.gz")}
    newest: dict[int, tuple[str, Path]] = {}
    for snap_id, listing_id, url in get_snapshot_ids():  # oldest first, so the last one wins
        page = files.get(str(snap_id))
        if page:
            newest[listing_id] = (url, page)
    return newest


def refresh(force: bool = False) -> dict[str, int]:
    """Re-read every listing's newest saved page once per PAGE_DATA_VERSION: price history and
    earlier sales are replaced with what it says, and photos are added where a listing has none.
    Returns how many listings got each."""
    from listingtracker import db
    from listingtracker.fetcher import get_adapter_for_url

    filled = {"price history": 0, "earlier sales": 0, "photos": 0, "area sales": 0}
    if not force and db.get_state(STATE_KEY) == str(PAGE_DATA_VERSION):
        return filled
    has_photos = set(db.get_first_image_urls())

    for listing_id, (url, path) in newest_pages().items():
        adapter = get_adapter_for_url(url)
        if not adapter:
            continue
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                html = f.read()
            parsed = adapter.parse(html, url=url)
            if parsed.get("list_date") or parsed.get("price_history"):
                db.save_price_history(listing_id, parsed.get("list_date"), parsed.get("price_history") or [])
                filled["price history"] += 1
            if "past_sales" in parsed:
                db.save_past_sales(listing_id, parsed["past_sales"])
                filled["earlier sales"] += bool(parsed["past_sales"])
            if parsed.get("area_sales"):
                db.save_area_sales(parsed["area_sales"])
                filled["area sales"] += len(parsed["area_sales"])
            if parsed.get("property_type"):
                db.fill_missing_property_type(listing_id, parsed["property_type"])
            if listing_id not in has_photos:
                urls = adapter.extract_images(html, url=url)
                if urls:
                    db.save_listing_images(listing_id, urls, source=adapter.site_name)
                    filled["photos"] += 1
        except Exception as e:  # one unreadable page shouldn't stop the rest
            logger.debug("Couldn't refresh listing %s from %s: %s", listing_id, path, e)

    db.set_state(STATE_KEY, str(PAGE_DATA_VERSION))
    logger.info("Refreshed from saved pages: %s", filled)
    return filled


def refresh_in_background() -> None:
    threading.Thread(target=refresh, name="saved-pages-refresh", daemon=True).start()

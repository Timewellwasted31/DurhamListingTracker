"""REST API server — serves listing data to the mobile app.

Run directly:
    python -m listingtracker.api

Or with uvicorn:
    uvicorn listingtracker.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import logging
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from listingtracker.paths import (
    ensure_directories, use_installed_browsers, DB_PATH, IMAGE_CACHE_DIR,
    DATA_DIR, LOGS_DIR, MPL_CACHE_DIR, ROOT,
)

use_installed_browsers()
os.environ["MPLCONFIGDIR"] = str(MPL_CACHE_DIR)

logger = logging.getLogger(__name__)

MOBILE_DIR = Path(__file__).parent / "mobile"

app = FastAPI(title="Durham Listing Tracker", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY_PATH = DATA_DIR / "api_key.txt"


def _load_or_create_api_key() -> str:
    """Load the API key from disk, or generate one on first run."""
    if API_KEY_PATH.exists():
        return API_KEY_PATH.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(32)
    API_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    API_KEY_PATH.write_text(key, encoding="utf-8")
    logger.info("Generated new API key — saved to %s", API_KEY_PATH)
    return key


_api_key: str = ""


def _get_api_key() -> str:
    global _api_key
    if not _api_key:
        _api_key = _load_or_create_api_key()
    return _api_key


async def verify_api_key(request: Request):
    """Check the X-API-Key header or ?key= query param."""
    key = request.headers.get("X-API-Key") or request.query_params.get("key")
    if not key or not hmac.compare_digest(key, _get_api_key()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# Static files: mobile app
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def mobile_index(request: Request):
    """Serve the mobile app — no auth required for the shell, API calls need the key."""
    index = MOBILE_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Mobile app not found")
    return HTMLResponse(index.read_text(encoding="utf-8"))


@app.get("/manifest.json")
async def manifest():
    f = MOBILE_DIR / "manifest.json"
    if f.exists():
        return JSONResponse(
            content=__import__("json").loads(f.read_text(encoding="utf-8")),
            media_type="application/manifest+json",
        )
    raise HTTPException(status_code=404)


@app.get("/sw.js")
async def service_worker():
    f = MOBILE_DIR / "sw.js"
    if f.exists():
        return FileResponse(f, media_type="application/javascript")
    raise HTTPException(status_code=404)


@app.get("/icon-192.png")
async def icon_192():
    f = MOBILE_DIR / "icon-192.png"
    if f.exists():
        return FileResponse(f, media_type="image/png")
    raise HTTPException(status_code=404)


@app.get("/icon-512.png")
async def icon_512():
    f = MOBILE_DIR / "icon-512.png"
    if f.exists():
        return FileResponse(f, media_type="image/png")
    raise HTTPException(status_code=404)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/listings")
async def list_listings(
    favorites_only: bool = False,
    city: Optional[str] = None,
    status: Optional[str] = None,
    sort: str = "date",
    _=Depends(verify_api_key),
):
    from listingtracker.db import get_all_listings, get_all_latest_snapshots, get_listing_images

    listings = get_all_listings(active_only=True)
    snapshots = get_all_latest_snapshots()

    results = []
    for lst in listings:
        if favorites_only and not lst.favorited:
            continue
        if city and lst.city.lower() != city.lower():
            continue

        snap = snapshots.get(lst.id)

        if status and snap:
            if snap.status.value.lower() != status.lower():
                continue

        images = get_listing_images(lst.id)
        first_image = images[0]["url"] if images else None

        results.append({
            "id": lst.id,
            "url": lst.url,
            "site": lst.site,
            "mls_number": lst.mls_number,
            "address": lst.address or "Unknown",
            "unit": lst.unit,
            "building_name": lst.building_name,
            "city": lst.city,
            "neighbourhood": lst.neighbourhood,
            "date_added": lst.date_added.isoformat() if lst.date_added else None,
            "favorited": lst.favorited,
            "image_url": first_image,
            "snapshot": _snap_dict(snap) if snap else None,
        })

    if sort == "price":
        results.sort(key=lambda r: (r["snapshot"] or {}).get("list_price") or 999999999)
    elif sort == "price_desc":
        results.sort(key=lambda r: (r["snapshot"] or {}).get("list_price") or 0, reverse=True)
    elif sort == "city":
        results.sort(key=lambda r: r["city"])
    else:
        results.sort(key=lambda r: r["date_added"] or "", reverse=True)

    return {"listings": results, "count": len(results)}


@app.get("/api/listings/{listing_id}")
async def get_listing(listing_id: int, _=Depends(verify_api_key)):
    from listingtracker.db import (
        get_listing_by_id, get_latest_snapshot, get_snapshots_for_listing,
        get_listing_images, get_peer_stats,
    )

    lst = get_listing_by_id(listing_id)
    if not lst:
        raise HTTPException(status_code=404, detail="Listing not found")

    snap = get_latest_snapshot(listing_id)
    history = get_snapshots_for_listing(listing_id)
    images = get_listing_images(listing_id)

    peers = {}
    if snap and snap.list_price and snap.beds:
        sqft = snap.sqft_max or snap.sqft_min or 0
        peers = get_peer_stats(
            listing_id, lst.city, snap.beds, snap.list_price, sqft
        )

    return {
        "id": lst.id,
        "url": lst.url,
        "site": lst.site,
        "mls_number": lst.mls_number,
        "address": lst.address,
        "unit": lst.unit,
        "building_name": lst.building_name,
        "city": lst.city,
        "neighbourhood": lst.neighbourhood,
        "date_added": lst.date_added.isoformat() if lst.date_added else None,
        "favorited": lst.favorited,
        "user_notes": lst.user_notes,
        "snapshot": _snap_dict(snap) if snap else None,
        "history": [_snap_dict(s) for s in history],
        "images": [img["url"] for img in images],
        "peer_stats": peers,
    }


@app.post("/api/listings/{listing_id}/favorite")
async def toggle_favorite(listing_id: int, _=Depends(verify_api_key)):
    from listingtracker.db import toggle_favorite as db_toggle

    new_state = db_toggle(listing_id)
    return {"listing_id": listing_id, "favorited": new_state}


@app.get("/api/listings/{listing_id}/comparables")
async def get_comparables(listing_id: int, _=Depends(verify_api_key)):
    from listingtracker.db import (
        get_listing_by_id, get_comparables_for_favorite, get_latest_snapshot,
        get_snapshots_for_listing,
    )
    from listingtracker.comparables import analyze_price_trend, predict_target_date, PRICE_TOLERANCE

    lst = get_listing_by_id(listing_id)
    if not lst:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not lst.favorited:
        return {"comparables": [], "message": "Only favorited listings have comparables"}

    fav_snap = get_latest_snapshot(listing_id)
    fav_price = fav_snap.list_price if fav_snap else 0

    comps = get_comparables_for_favorite(listing_id)
    result = []
    for c in comps:
        comp_snap = get_latest_snapshot(c["listing_id"])
        if not comp_snap or not comp_snap.list_price:
            continue

        history = get_snapshots_for_listing(c["listing_id"])
        trend = c.get("price_trend_pct_per_month")
        predicted = c.get("predicted_target_date")

        price_history = [
            {"date": s.fetched_at, "price": s.list_price}
            for s in history if s.list_price and s.list_price > 0
        ]

        gap = comp_snap.list_price - fav_price if fav_price else 0
        in_range = gap <= PRICE_TOLERANCE

        result.append({
            "listing_id": c["listing_id"],
            "address": c["address"],
            "city": c["city"],
            "url": c["url"],
            "similarity_score": c["similarity_score"],
            "current_price": comp_snap.list_price,
            "favorite_price": fav_price,
            "price_gap": gap,
            "in_range": in_range,
            "beds": comp_snap.beds,
            "baths": comp_snap.baths,
            "sqft": comp_snap.sqft_max or comp_snap.sqft_min,
            "trend_pct_month": trend,
            "predicted_target_date": predicted,
            "notes": c.get("notes", ""),
            "price_history": price_history,
        })

    return {"comparables": result, "favorite_price": fav_price, "tolerance": PRICE_TOLERANCE}


@app.get("/api/images/{listing_id}")
async def listing_images(listing_id: int, _=Depends(verify_api_key)):
    from listingtracker.db import get_listing_images
    images = get_listing_images(listing_id)
    return {"images": [img["url"] for img in images]}


@app.get("/api/image-proxy/{listing_id}/{index}")
async def image_proxy(listing_id: int, index: int, _=Depends(verify_api_key)):
    """Serve a locally-cached listing image."""
    img_dir = IMAGE_CACHE_DIR / str(listing_id)
    if not img_dir.exists():
        raise HTTPException(status_code=404, detail="No cached images")

    files = sorted(img_dir.iterdir())
    if index < 0 or index >= len(files):
        raise HTTPException(status_code=404, detail="Image index out of range")

    f = files[index]
    ext = f.suffix.lower()
    media = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
    return FileResponse(f, media_type=media)


@app.get("/api/stats")
async def stats(_=Depends(verify_api_key)):
    from listingtracker.db import get_all_listings, get_all_latest_snapshots
    from listingtracker.models import Status

    all_listings = get_all_listings(active_only=True)
    snaps = get_all_latest_snapshots()

    cities: dict[str, int] = {}
    statuses: dict[str, int] = {}
    total_favorited = 0
    prices = []

    for lst in all_listings:
        if lst.favorited:
            total_favorited += 1
        city = lst.city or "Unknown"
        cities[city] = cities.get(city, 0) + 1

        snap = snaps.get(lst.id)
        if snap:
            st = snap.status.value
            statuses[st] = statuses.get(st, 0) + 1
            if snap.list_price and snap.list_price > 10000:
                prices.append(snap.list_price)

    avg_price = sum(prices) / len(prices) if prices else 0
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0

    return {
        "total_listings": len(all_listings),
        "total_favorited": total_favorited,
        "cities": dict(sorted(cities.items(), key=lambda x: -x[1])),
        "statuses": statuses,
        "price_stats": {
            "average": round(avg_price),
            "min": round(min_price),
            "max": round(max_price),
            "count": len(prices),
        },
    }


@app.post("/api/scan-comparables")
async def scan_comparables(_=Depends(verify_api_key)):
    """Trigger a comparable property scan for all favorites."""
    from listingtracker.db import (
        get_favorited_listings, get_all_listings, get_all_latest_snapshots,
        get_latest_snapshot, get_snapshots_for_listing, upsert_comparable,
        delete_stale_comparables,
    )
    from listingtracker.comparables import (
        find_comparables_for_favorite, analyze_price_trend, predict_target_date,
        PRICE_TOLERANCE,
    )

    favorites = get_favorited_listings()
    if not favorites:
        return {"message": "No favorites to scan", "count": 0}

    all_listings = get_all_listings(active_only=True)
    all_snapshots = get_all_latest_snapshots()

    total = 0
    for fav in favorites:
        fav_snap = all_snapshots.get(fav.id) or get_latest_snapshot(fav.id)
        if not fav_snap or not fav_snap.list_price:
            continue

        matches = find_comparables_for_favorite(fav, fav_snap, all_listings, all_snapshots)
        for listing_id, score in matches:
            comp_snap = all_snapshots.get(listing_id)
            if not comp_snap:
                continue

            history = get_snapshots_for_listing(listing_id)
            trend = analyze_price_trend(history)
            predicted = None
            notes = ""

            if trend is not None and trend < 0:
                predicted = predict_target_date(
                    comp_snap.list_price, fav_snap.list_price, trend, PRICE_TOLERANCE,
                )
                if predicted:
                    notes = f"Dropping ~{abs(trend):.1f}%/mo, may reach target by {predicted}"

            gap = comp_snap.list_price - fav_snap.list_price
            if gap <= PRICE_TOLERANCE:
                notes = f"IN RANGE! Gap: ${gap:,.0f}"

            upsert_comparable(fav.id, listing_id, score,
                              predicted_target_date=predicted,
                              price_trend_pct_per_month=trend, notes=notes)
            total += 1

    stale = delete_stale_comparables()
    return {"message": f"Found {total} comparables across {len(favorites)} favorites", "count": total, "stale_removed": stale}


@app.get("/api/key-check")
async def key_check(_=Depends(verify_api_key)):
    return {"ok": True}


@app.get("/api/pair-info")
async def pair_info():
    """Return pairing data (server URL hint + API key) for QR code generation.
    No auth required — called by the desktop app itself to build the QR."""
    import socket
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    return {"url": f"http://{local_ip}:8000", "key": _get_api_key()}


def _snap_dict(snap) -> dict:
    if not snap:
        return {}
    return {
        "id": snap.id,
        "fetched_at": snap.fetched_at,
        "status": snap.status.value if hasattr(snap.status, "value") else str(snap.status),
        "list_price": snap.list_price,
        "maintenance_fee": snap.maintenance_fee,
        "taxes_annual": snap.taxes_annual,
        "beds": snap.beds,
        "baths": snap.baths,
        "sqft_min": snap.sqft_min,
        "sqft_max": snap.sqft_max,
        "parking": snap.parking,
        "locker": snap.locker,
        "balcony": snap.balcony,
        "exposure": snap.exposure,
        "days_on_market": snap.days_on_market,
        "site_estimate": snap.site_estimate,
        "broker": snap.broker,
        "fee_includes": snap.fee_includes,
        "property_type": snap.property_type,
        "year_built": snap.year_built,
        "ensuite_laundry": snap.ensuite_laundry,
        "outdoor_space": snap.outdoor_space,
        "possession_date": snap.possession_date,
        "rent_controlled": snap.rent_controlled,
        "power_of_sale": snap.power_of_sale,
    }


def setup_logging() -> None:
    from datetime import date
    log_file = LOGS_DIR / f"api-{date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Durham Listing Tracker API server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default 8000)")
    args = parser.parse_args()

    ensure_directories()
    setup_logging()

    from listingtracker.db import init_db
    init_db()

    api_key = _get_api_key()
    logger.info("API server starting on %s:%d", args.host, args.port)
    logger.info("API key: %s", api_key)
    logger.info("Mobile app: http://localhost:%d", args.port)

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

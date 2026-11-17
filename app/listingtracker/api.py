"""REST API server — serves listing data to the mobile app.

Run directly:
    python -m listingtracker.api

Or with uvicorn:
    uvicorn listingtracker.api:app --host 0.0.0.0 --port 8000

Endpoints that touch the database are plain `def`, so FastAPI runs them in its thread pool
instead of blocking the event loop that both the HTTP and HTTPS servers share.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response

from listingtracker.paths import ensure_directories, use_installed_browsers, LOGS_DIR, MPL_CACHE_DIR
from listingtracker.remote import api_key

use_installed_browsers()
os.environ["MPLCONFIGDIR"] = str(MPL_CACHE_DIR)

logger = logging.getLogger(__name__)

MOBILE_DIR = Path(__file__).parent / "mobile"
KEEP_ALIVE_SECONDS = 60  # uvicorn's 5 s default forces a fresh TLS handshake after a short pause

app = FastAPI(title="Durham Listing Tracker", version="1.0.0")
# Images are excluded by the middleware's defaults, so only JSON and the page are compressed
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key"],
)

_http_port: int = 8000
_remote = None  # listingtracker.remote.RemoteAccess once main() has started HTTPS


async def verify_api_key(request: Request):
    key = request.headers.get("X-API-Key")
    if not key or not hmac.compare_digest(key, api_key()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _compact(d: dict) -> dict:
    """Drop empty fields; the page treats a missing field the same as null."""
    return {k: v for k, v in d.items() if v is not None}


# ---------------------------------------------------------------------------
# Static files: mobile app (no key needed; every API call needs it)
# ---------------------------------------------------------------------------

def _serve_static(path: str, filename: str, cache: str, media_type: str | None = None) -> None:
    def serve(request: Request):
        f = MOBILE_DIR / filename
        try:
            st = f.stat()
        except FileNotFoundError:
            raise HTTPException(status_code=404) from None
        resp = FileResponse(f, stat_result=st, media_type=media_type, headers={"Cache-Control": cache})
        # FileResponse sends an ETag but never answers 304 itself
        if request.headers.get("if-none-match") == resp.headers["etag"]:
            return Response(status_code=304, headers={"ETag": resp.headers["etag"], "Cache-Control": cache})
        return resp

    app.add_api_route(path, serve, methods=["GET"], include_in_schema=False)


# no-cache = revalidate each time (a 304 when unchanged), so updates reach phones at once
for _path, _file, _cache, _media in (
    ("/", "index.html", "no-cache", None),
    ("/sw.js", "sw.js", "no-cache", None),
    ("/manifest.json", "manifest.json", "max-age=86400", "application/manifest+json"),
    ("/icon-192.png", "icon-192.png", "max-age=86400", None),
    ("/icon-512.png", "icon-512.png", "max-age=86400", None),
):
    _serve_static(_path, _file, _cache, _media)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/listings")
def list_listings(_=Depends(verify_api_key)):
    """Every active listing in summary form; the page filters, sorts and totals them itself."""
    from listingtracker.db import get_all_listings, get_all_latest_snapshots, get_first_image_urls

    snapshots = get_all_latest_snapshots()
    images = get_first_image_urls()
    return {"listings": [
        _summary(lst, snapshots.get(lst.id), images.get(lst.id))
        for lst in get_all_listings(active_only=True)
    ]}


def _summary(lst, snap, image_url) -> dict:
    card = {
        "id": lst.id,
        "address": lst.address or "Unknown",
        "unit": lst.unit,
        "city": lst.city,
        "site": lst.site,
        "favorited": lst.favorited,
        "date_added": lst.date_added.isoformat() if lst.date_added else None,
        "image_url": image_url,
    }
    if snap:
        card.update(
            status=snap.status.value,
            price=snap.list_price,
            beds=snap.beds,
            baths=snap.baths,
            sqft=snap.sqft_max or snap.sqft_min,
            type=snap.property_type,
        )
    return _compact(card)


@app.get("/api/listings/{listing_id}")
def get_listing(listing_id: int, _=Depends(verify_api_key)):
    from listingtracker.db import get_listing_by_id, get_snapshots_for_listing, get_listing_images, get_peer_stats

    lst = get_listing_by_id(listing_id)
    if not lst:
        raise HTTPException(status_code=404, detail="Listing not found")

    history = get_snapshots_for_listing(listing_id)  # oldest first
    snap = history[-1] if history else None

    peers = {}
    if snap and snap.list_price and snap.beds:
        sqft = snap.sqft_max or snap.sqft_min or 0
        peers = get_peer_stats(listing_id, lst.city, snap.beds, snap.list_price, sqft)

    return _compact({
        **asdict(lst),
        "snapshot": _snap_dict(snap) if snap else None,
        "history": [{"fetched_at": s.fetched_at, "list_price": s.list_price} for s in history if s.list_price],
        "images": [img["url"] for img in get_listing_images(listing_id)],
        "peer_stats": peers,
    })


_SNAPSHOT_INTERNAL = ("id", "listing_id", "raw_html_path", "parse_errors")


def _snap_dict(snap) -> dict:
    d = asdict(snap)
    for field in _SNAPSHOT_INTERNAL:
        del d[field]
    d["status"] = snap.status.value
    return _compact(d)


@app.post("/api/listings/{listing_id}/favorite")
def toggle_favorite(listing_id: int, _=Depends(verify_api_key)):
    from listingtracker.db import toggle_favorite as db_toggle

    return {"listing_id": listing_id, "favorited": db_toggle(listing_id)}


@app.get("/api/listings/{listing_id}/comparables")
def get_comparables(listing_id: int, _=Depends(verify_api_key)):
    from listingtracker.db import get_listing_by_id, get_comparables_for_favorite, get_latest_snapshot
    from listingtracker.comparables import PRICE_TOLERANCE

    lst = get_listing_by_id(listing_id)
    if not lst:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not lst.favorited:
        return {"comparables": [], "message": "Only favorited listings have comparables"}

    fav_snap = get_latest_snapshot(listing_id)
    fav_price = fav_snap.list_price if fav_snap else 0

    result = []
    for c in get_comparables_for_favorite(listing_id):
        comp_snap = get_latest_snapshot(c["listing_id"])
        if not comp_snap or not comp_snap.list_price:
            continue
        gap = comp_snap.list_price - fav_price if fav_price else 0
        result.append(_compact({
            "listing_id": c["listing_id"],
            "address": c["address"],
            "city": c["city"],
            "similarity_score": c["similarity_score"],
            "current_price": comp_snap.list_price,
            "price_gap": gap,
            "in_range": gap <= PRICE_TOLERANCE,
            "beds": comp_snap.beds,
            "baths": comp_snap.baths,
            "sqft": comp_snap.sqft_max or comp_snap.sqft_min,
            "trend_pct_month": c.get("price_trend_pct_per_month"),
            "predicted_target_date": c.get("predicted_target_date"),
        }))

    return {"comparables": result, "favorite_price": fav_price, "tolerance": PRICE_TOLERANCE}


@app.post("/api/scan-comparables")
def scan_comparables(_=Depends(verify_api_key)):
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

            trend = analyze_price_trend(get_snapshots_for_listing(listing_id))
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
def key_check(_=Depends(verify_api_key)):
    """Confirms the key, and tells a paired device where the server can currently be reached."""
    return {"ok": True, "urls": _remote.urls() if _remote else []}


@app.get("/api/pair-info")
def pair_info(request: Request):
    """Pairing data for the desktop app's QR code. It includes the API key, so it only answers
    requests made from this computer."""
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Pairing data is only available on this computer")
    from listingtracker.remote import lan_ip
    info = {"url": f"http://{lan_ip()}:{_http_port}", "key": api_key()}
    if _remote:
        info.update(
            urls=_remote.urls(),
            fp=_remote.fingerprint,
            remote={"status": _remote.status, "message": _remote.message},
        )
    return info


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
    parser.add_argument("--https-port", type=int, default=8443,
                        help="Encrypted port for paired devices (default 8443)")
    parser.add_argument("--no-upnp", action="store_true",
                        help="Do not ask the router to forward the HTTPS port")
    args = parser.parse_args()

    ensure_directories()
    setup_logging()

    from listingtracker.db import init_db
    init_db()

    from listingtracker.remote import CERT_PATH, KEY_PATH, RemoteAccess, ensure_certificate

    global _http_port, _remote
    _http_port = args.port
    _remote = RemoteAccess(args.https_port, ensure_certificate(), use_upnp=not args.no_upnp)
    logger.info("API server starting on %s:%d (HTTPS on %d)", args.host, args.port, args.https_port)
    logger.info("API key: %s", api_key())
    logger.info("Mobile app: http://localhost:%d", args.port)

    # proxy_headers off: the loopback check in pair_info must see the real peer address
    common = dict(host=args.host, log_level="info", proxy_headers=False,
                  timeout_keep_alive=KEEP_ALIVE_SECONDS)
    servers = [
        uvicorn.Server(uvicorn.Config(app, port=args.port, **common)),
        uvicorn.Server(uvicorn.Config(app, port=args.https_port, **common,
                                      ssl_certfile=str(CERT_PATH), ssl_keyfile=str(KEY_PATH))),
    ]

    async def serve() -> None:
        await asyncio.gather(*(s.serve() for s in servers))

    _remote.start()
    try:
        asyncio.run(serve())
    finally:
        _remote.stop()


if __name__ == "__main__":
    main()

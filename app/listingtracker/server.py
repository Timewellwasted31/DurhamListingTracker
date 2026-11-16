"""Background server — periodically rechecks favorited listings.

Run directly:
    python -m listingtracker.server

Or as a Windows scheduled task / startup item.
The server sleeps until the next scheduled check time (default 07:00),
then fetches all favorited listings and goes back to sleep.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path

from listingtracker.paths import (
    ensure_directories, use_installed_browsers, LOGS_DIR, MPL_CACHE_DIR, DB_PATH,
)

use_installed_browsers()
os.environ["MPLCONFIGDIR"] = str(MPL_CACHE_DIR)

LOCK_PATH = DB_PATH.parent / "server.lock"


def setup_logging() -> None:
    log_file = LOGS_DIR / f"server-{date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _acquire_lock() -> bool:
    """Simple file lock to prevent duplicate server instances."""
    try:
        if LOCK_PATH.exists():
            pid = LOCK_PATH.read_text().strip()
            if pid.isdigit() and _pid_alive(int(pid)):
                return False
        LOCK_PATH.write_text(str(os.getpid()))
        return True
    except OSError:
        return False


def _release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _seconds_until(hour: int, minute: int = 0) -> float:
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def run_check(logger: logging.Logger) -> None:
    """Fetch all favorited listings and update comparable property tracking."""
    from listingtracker.db import (
        init_db, get_favorited_listings, cleanup_bad_listings,
        get_all_listings, get_all_latest_snapshots, get_latest_snapshot,
        get_snapshots_for_listing, upsert_comparable, delete_stale_comparables,
    )
    from listingtracker.fetcher import fetch_all
    from listingtracker.comparables import (
        find_comparables_for_favorite, analyze_price_trend, predict_target_date,
        PRICE_TOLERANCE,
    )

    init_db()
    favorites = get_favorited_listings()
    if not favorites:
        logger.info("No favorited listings to check")
        return

    logger.info("Checking %d favorited listings", len(favorites))

    def on_progress(i, total, address):
        logger.info("  [%d/%d] %s", i + 1, total, address)

    snaps, changes = fetch_all(
        favorites,
        headless=True,
        progress_callback=on_progress,
        show_browser=False,
    )

    removed = cleanup_bad_listings(after_fetch=True)
    logger.info(
        "Done: %d fetched, %d changes, %d cleaned up",
        len(snaps), len(changes), removed,
    )

    if changes:
        _notify_changes(changes, logger)

    # --- Comparable property tracking ---
    logger.info("Scanning for comparable properties...")
    all_listings = get_all_listings(active_only=True)
    all_snapshots = get_all_latest_snapshots()

    comp_count = 0
    drop_alerts = []
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

            # Check if comp already dropped into range
            gap = comp_snap.list_price - fav_snap.list_price
            if gap <= PRICE_TOLERANCE:
                comp_listing = next((l for l in all_listings if l.id == listing_id), None)
                addr = comp_listing.address if comp_listing else f"ID {listing_id}"
                drop_alerts.append(
                    f"{addr}: now ${comp_snap.list_price:,.0f} "
                    f"(was +${gap + PRICE_TOLERANCE:,.0f} above your ${fav_snap.list_price:,.0f} fav)"
                )
                notes = f"IN RANGE! Gap: ${gap:,.0f}"

            upsert_comparable(
                fav.id, listing_id, score,
                predicted_target_date=predicted,
                price_trend_pct_per_month=trend,
                notes=notes,
            )
            comp_count += 1

    stale = delete_stale_comparables()
    logger.info("Comparables: %d tracked, %d stale removed", comp_count, stale)

    if drop_alerts:
        _notify_price_drops(drop_alerts, logger)


def _notify_changes(changes: list, logger: logging.Logger) -> None:
    """Show a desktop notification summarizing changes to favorites."""
    try:
        from plyer import notification
        summary_parts = []
        for c in changes[:5]:
            addr = c.listing.address or "Unknown"
            summary_parts.append(f"{addr}: {c.field} {c.description}")
        msg = "\n".join(summary_parts)
        if len(changes) > 5:
            msg += f"\n... and {len(changes) - 5} more"
        notification.notify(
            title="Durham Listing Tracker",
            message=msg,
            timeout=10,
        )
    except Exception as e:
        logger.debug("Desktop notification failed: %s", e)


def _notify_price_drops(alerts: list[str], logger: logging.Logger) -> None:
    """Notify when comparable properties drop into the target price range."""
    try:
        from plyer import notification
        msg = "\n".join(alerts[:5])
        if len(alerts) > 5:
            msg += f"\n... and {len(alerts) - 5} more"
        notification.notify(
            title="Price Drop Alert!",
            message=msg,
            timeout=15,
        )
    except Exception as e:
        logger.debug("Desktop notification failed: %s", e)
    logger.info("PRICE DROP ALERTS:\n%s", "\n".join(alerts))


def main() -> None:
    parser = argparse.ArgumentParser(description="Durham Listing Tracker background server")
    parser.add_argument("--hour", type=int, default=7, help="Hour to run daily check (0-23, default 7)")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=int, default=0,
                        help="Run every N hours instead of at a fixed time (0 = daily at --hour)")
    args = parser.parse_args()

    ensure_directories()
    setup_logging()
    logger = logging.getLogger("listingtracker.server")

    if not _acquire_lock():
        logger.error("Another server instance is already running")
        sys.exit(1)

    try:
        logger.info("Background server started (PID %d)", os.getpid())

        if args.once:
            run_check(logger)
            return

        if args.interval > 0:
            logger.info("Running every %d hours", args.interval)
            while True:
                run_check(logger)
                time.sleep(args.interval * 3600)
        else:
            logger.info("Scheduled daily at %02d:00", args.hour)
            while True:
                wait = _seconds_until(args.hour)
                logger.info("Next check in %.0f minutes", wait / 60)
                time.sleep(wait)
                run_check(logger)
                time.sleep(60)  # avoid double-firing in the same minute
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    finally:
        _release_lock()


if __name__ == "__main__":
    main()

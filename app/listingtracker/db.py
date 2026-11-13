"""SQLite database layer — append-only history."""

from __future__ import annotations

import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

from listingtracker.paths import DB_PATH
from listingtracker.models import Listing, Snapshot, Status
from listingtracker.adapters.base import clean_text

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    site TEXT NOT NULL,
    mls_number TEXT,
    address TEXT NOT NULL DEFAULT '',
    unit TEXT,
    building_name TEXT,
    city TEXT NOT NULL DEFAULT '',
    neighbourhood TEXT,
    date_added TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    user_notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'UNKNOWN',
    list_price REAL,
    maintenance_fee REAL,
    taxes_annual REAL,
    beds INTEGER,
    baths INTEGER,
    sqft_min INTEGER,
    sqft_max INTEGER,
    parking TEXT,
    locker TEXT,
    balcony TEXT,
    exposure TEXT,
    days_on_market INTEGER,
    site_estimate REAL,
    broker TEXT,
    fee_includes TEXT,
    property_type TEXT,
    year_built INTEGER,
    ensuite_laundry TEXT,
    outdoor_space TEXT,
    possession_date TEXT,
    rent_controlled TEXT,
    power_of_sale INTEGER,
    raw_html_path TEXT,
    parse_errors TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_listing ON snapshots(listing_id, fetched_at DESC);
"""

SEED_URLS = [
    "https://strata.ca/pickering/1210-radom-st-the-bayshore-condominiums",
    "https://strata.ca/whitby/711-rossland-rd-e-the-waldorf-condos",
]


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _connect():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)

        row = conn.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
            conn.commit()
            logger.info("Database initialized at version %d", SCHEMA_VERSION)
            _seed_listings(conn)
        else:
            current = row["version"]
            logger.info("Database at version %d", current)
            if current < 2:
                _migrate_v1_to_v2(conn)
                conn.execute("UPDATE schema_version SET version = 2")
                conn.commit()
                logger.info("Migrated database to version 2")
                current = 2
            if current < 3:
                _migrate_v2_to_v3(conn)
                conn.execute("UPDATE schema_version SET version = 3")
                conn.commit()
                logger.info("Migrated database to version 3")


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)").fetchall()}
    for col, coltype in [("property_type", "TEXT"), ("year_built", "INTEGER"),
                         ("ensuite_laundry", "TEXT"), ("outdoor_space", "TEXT"),
                         ("possession_date", "TEXT"), ("rent_controlled", "TEXT")]:
        if col not in existing:
            conn.execute(f"ALTER TABLE snapshots ADD COLUMN {col} {coltype}")
    conn.commit()


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)").fetchall()}
    if "power_of_sale" not in existing:
        conn.execute("ALTER TABLE snapshots ADD COLUMN power_of_sale INTEGER")
    conn.commit()


def _seed_listings(conn: sqlite3.Connection) -> None:
    now = datetime.utcnow().isoformat()
    for url in SEED_URLS:
        site = "strata.ca"
        existing = conn.execute(
            "SELECT id FROM listings WHERE url = ?", (url,)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO listings (url, site, address, city, date_added) "
                "VALUES (?, ?, '', '', ?)",
                (url, site, now),
            )
            logger.info("Seeded listing: %s", url)
    conn.commit()


def add_listing(url: str, site: str) -> Optional[Listing]:
    url = normalize_url(url)
    with _connect() as conn:
        now = datetime.utcnow().isoformat()
        cursor = conn.execute(
            "INSERT OR IGNORE INTO listings (url, site, address, city, date_added) "
            "VALUES (?, ?, '', '', ?)",
            (url, site, now),
        )
        conn.commit()
        if cursor.lastrowid == 0 or cursor.rowcount == 0:
            return None
        listing = Listing(
            id=cursor.lastrowid,
            url=url,
            site=site,
            date_added=datetime.fromisoformat(now),
        )
        return listing


def get_all_listings(active_only: bool = True) -> list[Listing]:
    with _connect() as conn:
        query = "SELECT * FROM listings"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY date_added DESC"
        rows = conn.execute(query).fetchall()
        return [_row_to_listing(r) for r in rows]


def get_listing_by_id(listing_id: int) -> Optional[Listing]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        return _row_to_listing(row) if row else None


def get_listing_by_url(url: str) -> Optional[Listing]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM listings WHERE url = ?", (url,)
        ).fetchone()
        return _row_to_listing(row) if row else None


def update_listing(listing: Listing) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE listings SET mls_number=?, address=?, unit=?, "
            "building_name=?, city=?, neighbourhood=?, active=?, user_notes=? "
            "WHERE id=?",
            (
                listing.mls_number,
                listing.address,
                listing.unit,
                listing.building_name,
                listing.city,
                listing.neighbourhood,
                1 if listing.active else 0,
                listing.user_notes,
                listing.id,
            ),
        )
        conn.commit()


def add_snapshot(snapshot: Snapshot) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO snapshots "
            "(listing_id, fetched_at, status, list_price, maintenance_fee, "
            "taxes_annual, beds, baths, sqft_min, sqft_max, parking, locker, "
            "balcony, exposure, days_on_market, site_estimate, broker, "
            "fee_includes, property_type, year_built, ensuite_laundry, outdoor_space, "
            "possession_date, rent_controlled, power_of_sale, raw_html_path, parse_errors) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                snapshot.listing_id,
                snapshot.fetched_at or datetime.utcnow().isoformat(),
                snapshot.status.value,
                snapshot.list_price,
                snapshot.maintenance_fee,
                snapshot.taxes_annual,
                snapshot.beds,
                snapshot.baths,
                snapshot.sqft_min,
                snapshot.sqft_max,
                snapshot.parking,
                snapshot.locker,
                snapshot.balcony,
                snapshot.exposure,
                snapshot.days_on_market,
                snapshot.site_estimate,
                snapshot.broker,
                snapshot.fee_includes,
                snapshot.property_type,
                snapshot.year_built,
                snapshot.ensuite_laundry,
                snapshot.outdoor_space,
                snapshot.possession_date,
                snapshot.rent_controlled,
                1 if snapshot.power_of_sale else (0 if snapshot.power_of_sale is False else None),
                snapshot.raw_html_path,
                snapshot.parse_errors,
            ),
        )
        conn.commit()
        return cursor.lastrowid


_UPDATABLE_SNAPSHOT_FIELDS = {
    "list_price", "maintenance_fee", "taxes_annual", "beds", "baths",
    "sqft_min", "sqft_max", "parking", "locker", "balcony", "exposure",
    "days_on_market", "site_estimate", "broker", "fee_includes",
    "property_type", "year_built", "ensuite_laundry", "outdoor_space",
    "possession_date", "rent_controlled",
}


def update_snapshot_fields(snapshot_id: int, fields: dict) -> None:
    """Update specific fields on an existing snapshot."""
    updates = {k: v for k, v in fields.items() if k in _UPDATABLE_SNAPSHOT_FIELDS}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [snapshot_id]
    with _connect() as conn:
        conn.execute(f"UPDATE snapshots SET {set_clause} WHERE id = ?", values)
        conn.commit()


def get_latest_snapshot(listing_id: int) -> Optional[Snapshot]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM snapshots WHERE listing_id = ? "
            "ORDER BY fetched_at DESC LIMIT 1",
            (listing_id,),
        ).fetchone()
        return _row_to_snapshot(row) if row else None


def get_all_latest_snapshots() -> dict[int, Snapshot]:
    """Return {listing_id: latest_snapshot} for all listings in one query."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT s.* FROM snapshots s "
            "INNER JOIN ("
            "  SELECT listing_id, MAX(fetched_at) AS max_ts "
            "  FROM snapshots GROUP BY listing_id"
            ") latest ON s.listing_id = latest.listing_id "
            "AND s.fetched_at = latest.max_ts"
        ).fetchall()
        return {r["listing_id"]: _row_to_snapshot(r) for r in rows}


def get_previous_snapshot(listing_id: int) -> Optional[Snapshot]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM snapshots WHERE listing_id = ? "
            "ORDER BY fetched_at DESC LIMIT 1 OFFSET 1",
            (listing_id,),
        ).fetchone()
        return _row_to_snapshot(row) if row else None


def get_snapshots_for_listing(listing_id: int) -> list[Snapshot]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE listing_id = ? "
            "ORDER BY fetched_at ASC",
            (listing_id,),
        ).fetchall()
        return [_row_to_snapshot(r) for r in rows]


def get_peer_stats(
    listing_id: int,
    city: str = "",
    beds: int = 0,
    list_price: float = 0,
    sqft: int = 0,
) -> dict:
    """Return price-per-sqft stats for peer listings (same beds, same city, FOR_SALE).

    When city/beds/list_price/sqft are provided, they're used directly instead of
    re-querying the DB for data the caller already has.
    """
    with _connect() as conn:
        if not city or not beds or not list_price or not sqft:
            row = conn.execute(
                "SELECT l.city FROM listings l WHERE l.id = ?", (listing_id,)
            ).fetchone()
            if not row:
                return {}
            city = city or row["city"]
            if not beds or not list_price or not sqft:
                snap = get_latest_snapshot(listing_id)
                if not snap or not snap.list_price or not snap.beds:
                    return {}
                beds = beds or snap.beds
                list_price = list_price or snap.list_price
                sqft = sqft or snap.sqft_max or snap.sqft_min or 0

        if not city or not beds or not list_price or not sqft or sqft <= 0:
            return {}

        this_ppsf = list_price / sqft

        rows = conn.execute(
            "SELECT s.list_price, s.sqft_min, s.sqft_max, s.listing_id "
            "FROM snapshots s "
            "JOIN listings l ON l.id = s.listing_id "
            "WHERE l.city = ? AND s.beds = ? AND s.status = 'FOR_SALE' "
            "AND s.list_price > 0 AND (s.sqft_max > 0 OR s.sqft_min > 0) "
            "AND s.id = ("
            "  SELECT s2.id FROM snapshots s2 "
            "  WHERE s2.listing_id = s.listing_id "
            "  ORDER BY s2.fetched_at DESC LIMIT 1"
            ")",
            (city, beds),
        ).fetchall()

        ppsf_values = []
        for r in rows:
            sf = r["sqft_max"] or r["sqft_min"]
            if sf and sf > 0:
                ppsf_values.append(r["list_price"] / sf)

        if len(ppsf_values) < 2:
            return {}

        avg_ppsf = sum(ppsf_values) / len(ppsf_values)
        discount_pct = (1 - this_ppsf / avg_ppsf) * 100 if avg_ppsf > 0 else 0

        return {
            "this_ppsf": this_ppsf,
            "avg_ppsf": avg_ppsf,
            "discount_pct": discount_pct,
            "peer_count": len(ppsf_values),
            "city": city,
            "beds": beds,
        }


def delete_listing(listing_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM snapshots WHERE listing_id = ?", (listing_id,))
        conn.execute("DELETE FROM listings WHERE id = ?", (listing_id,))
        conn.commit()


def cleanup_empty_listings() -> int:
    """Delete listings that have no address and no snapshots (failed fetches)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT l.id FROM listings l "
            "LEFT JOIN snapshots s ON l.id = s.listing_id "
            "WHERE l.address = '' AND s.id IS NULL"
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM listings WHERE id IN ({placeholders})", ids)
            conn.commit()
        return len(ids)


def cleanup_bad_listings(after_fetch: bool = False) -> int:
    """Delete listings that have no useful data, and deduplicate.

    By default, skips listings added in the last 2 hours so newly discovered
    listings survive long enough to be fetched. Pass after_fetch=True to skip
    the grace period (used after a fetch pass when we know fetching was attempted).
    """
    with _connect() as conn:
        ids_to_delete = set()

        if after_fetch:
            rows = conn.execute(
                "SELECT l.id FROM listings l "
                "LEFT JOIN snapshots s ON l.id = s.listing_id "
                "WHERE l.address = '' AND s.id IS NULL"
            ).fetchall()
            ids_to_delete.update(r["id"] for r in rows)

            rows = conn.execute(
                "SELECT l.id FROM listings l "
                "WHERE l.address = '' AND NOT EXISTS ("
                "  SELECT 1 FROM snapshots s WHERE s.listing_id = l.id "
                "  AND s.list_price IS NOT NULL AND s.list_price > 0"
                ")"
            ).fetchall()
            ids_to_delete.update(r["id"] for r in rows)
        else:
            cutoff = (datetime.utcnow() - timedelta(hours=2)).isoformat()

            rows = conn.execute(
                "SELECT l.id FROM listings l "
                "LEFT JOIN snapshots s ON l.id = s.listing_id "
                "WHERE l.address = '' AND s.id IS NULL "
                "AND l.date_added < ?",
                (cutoff,),
            ).fetchall()
            ids_to_delete.update(r["id"] for r in rows)

            rows = conn.execute(
                "SELECT l.id FROM listings l "
                "WHERE l.address = '' AND l.date_added < ? AND NOT EXISTS ("
                "  SELECT 1 FROM snapshots s WHERE s.listing_id = l.id "
                "  AND s.list_price IS NOT NULL AND s.list_price > 0"
                ")",
                (cutoff,),
            ).fetchall()
            ids_to_delete.update(r["id"] for r in rows)

        # 3) Anomalous prices: list_price > 0 but < 10000 is clearly bad data
        rows = conn.execute(
            "SELECT DISTINCT s.listing_id FROM snapshots s "
            "WHERE s.list_price > 0 AND s.list_price < 10000"
        ).fetchall()
        ids_to_delete.update(r["listing_id"] for r in rows)

        # 4) Deduplicate: same normalized URL, keep the oldest (lowest id)
        all_listings = conn.execute(
            "SELECT id, url FROM listings ORDER BY id"
        ).fetchall()
        seen_urls = {}
        for r in all_listings:
            norm = normalize_url(r["url"])
            if norm in seen_urls:
                ids_to_delete.add(r["id"])
            else:
                seen_urls[norm] = r["id"]

        ids = list(ids_to_delete)
        if ids:
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"DELETE FROM snapshots WHERE listing_id IN ({placeholders})", ids
            )
            conn.execute(
                f"DELETE FROM listings WHERE id IN ({placeholders})", ids
            )
            remaining = conn.execute("SELECT id, url FROM listings").fetchall()
            for r in remaining:
                normed = normalize_url(r["url"])
                if normed != r["url"]:
                    conn.execute(
                        "UPDATE listings SET url = ? WHERE id = ?",
                        (normed, r["id"]),
                    )
            conn.commit()
        return len(ids)


def cleanup_non_purchasable() -> int:
    """Delete listings that a buyer can't purchase.

    Removes single-snapshot listings that are:
    - SOLD, TERMINATED, UNKNOWN, or BLOCKED status
    - Have no price (NULL or 0) regardless of status
    - Have no address and no price

    Only targets listings with exactly one snapshot (freshly discovered).
    Listings with multiple snapshots (history) are kept.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT l.id FROM listings l "
            "JOIN snapshots s ON s.listing_id = l.id "
            "GROUP BY l.id "
            "HAVING COUNT(s.id) = 1 "
            "AND ("
            "  MAX(s.status) IN ('SOLD', 'TERMINATED', 'UNKNOWN', 'BLOCKED')"
            "  OR MAX(COALESCE(s.list_price, 0)) <= 0"
            ")"
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            ph = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM snapshots WHERE listing_id IN ({ph})", ids)
            conn.execute(f"DELETE FROM listings WHERE id IN ({ph})", ids)
            conn.commit()
        return len(ids)


def normalize_url(url: str) -> str:
    """Normalize a listing URL for dedup: strip trailing slashes/backslashes, fragments."""
    url = url.strip().rstrip("/").rstrip("\\")
    if "#" in url:
        url = url.split("#")[0]
    return url


def get_consecutive_blocked_count(listing_id: int) -> int:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status FROM snapshots WHERE listing_id = ? "
            "ORDER BY fetched_at DESC LIMIT 3",
            (listing_id,),
        ).fetchall()
        count = 0
        for row in rows:
            if row["status"] == Status.BLOCKED.value:
                count += 1
            else:
                break
        return count


def _row_to_listing(row: sqlite3.Row) -> Listing:
    return Listing(
        id=row["id"],
        url=row["url"],
        site=row["site"],
        mls_number=row["mls_number"],
        address=row["address"],
        unit=row["unit"],
        building_name=row["building_name"],
        city=row["city"],
        neighbourhood=row["neighbourhood"],
        date_added=datetime.fromisoformat(row["date_added"]) if row["date_added"] else None,
        active=bool(row["active"]),
        user_notes=row["user_notes"],
    )


def _row_to_snapshot(row: sqlite3.Row) -> Snapshot:
    keys = row.keys()
    return Snapshot(
        id=row["id"],
        listing_id=row["listing_id"],
        fetched_at=row["fetched_at"],
        status=Status(row["status"]) if row["status"] else Status.UNKNOWN,
        list_price=row["list_price"],
        maintenance_fee=row["maintenance_fee"],
        taxes_annual=row["taxes_annual"],
        beds=row["beds"],
        baths=row["baths"],
        sqft_min=row["sqft_min"],
        sqft_max=row["sqft_max"],
        parking=clean_text(row["parking"]),
        locker=clean_text(row["locker"]),
        balcony=clean_text(row["balcony"]),
        exposure=clean_text(row["exposure"]),
        days_on_market=row["days_on_market"],
        site_estimate=row["site_estimate"],
        broker=clean_text(row["broker"]),
        fee_includes=clean_text(row["fee_includes"]),
        property_type=clean_text(row["property_type"]) if "property_type" in keys else None,
        year_built=row["year_built"] if "year_built" in keys else None,
        ensuite_laundry=clean_text(row["ensuite_laundry"]) if "ensuite_laundry" in keys else None,
        outdoor_space=clean_text(row["outdoor_space"]) if "outdoor_space" in keys else None,
        possession_date=clean_text(row["possession_date"]) if "possession_date" in keys else None,
        rent_controlled=clean_text(row["rent_controlled"]) if "rent_controlled" in keys else None,
        power_of_sale=bool(row["power_of_sale"]) if "power_of_sale" in keys and row["power_of_sale"] is not None else None,
        raw_html_path=row["raw_html_path"],
        parse_errors=row["parse_errors"],
    )

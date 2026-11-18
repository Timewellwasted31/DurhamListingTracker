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

SCHEMA_VERSION = 6

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
    favorited INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS listing_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    url TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    local_path TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    UNIQUE(listing_id, url)
);

CREATE INDEX IF NOT EXISTS idx_listing_images_listing ON listing_images(listing_id, position);

CREATE TABLE IF NOT EXISTS comparables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    favorite_id INTEGER NOT NULL REFERENCES listings(id),
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    similarity_score REAL NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_checked TEXT,
    predicted_target_date TEXT,
    price_trend_pct_per_month REAL,
    notes TEXT NOT NULL DEFAULT '',
    UNIQUE(favorite_id, listing_id)
);

CREATE INDEX IF NOT EXISTS idx_comparables_favorite ON comparables(favorite_id);

-- The listing site's own record: kind 'listed' holds the date the current listing started,
-- kind 'price' each asking price and the date it took effect. Snapshots only show what we saw.
CREATE TABLE IF NOT EXISTS price_events (
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    kind TEXT NOT NULL,
    event_date TEXT NOT NULL,
    price REAL,
    PRIMARY KEY (listing_id, kind, event_date)
) WITHOUT ROWID;

-- Earlier sales of the same home, as the listing site records them
CREATE TABLE IF NOT EXISTS past_sales (
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    sold_date TEXT NOT NULL,
    sold_price REAL NOT NULL,
    PRIMARY KEY (listing_id, sold_date)
) WITHOUT ROWID;

-- Sales of homes in the area (comparables the listing pages show, and the sale records of their
-- buildings), used to estimate what a seller paid when their own purchase isn't on record
CREATE TABLE IF NOT EXISTS area_sales (
    sale_key TEXT PRIMARY KEY,
    sold_date TEXT NOT NULL,
    sold_price REAL NOT NULL,
    city TEXT,
    address TEXT,
    type TEXT,
    beds INTEGER,
    sqft INTEGER
) WITHOUT ROWID;

-- Sales of units in a building, from strata.ca's building history (read with the user's account).
-- `building` is Strata's page path for it, e.g. "whitby/712-rossland-rd-e-connoisseur-condos".
CREATE TABLE IF NOT EXISTS building_sales (
    building TEXT NOT NULL,
    unit TEXT NOT NULL,
    sold_date TEXT NOT NULL,
    sold_price REAL NOT NULL,
    address TEXT,
    list_price REAL,
    list_date TEXT,
    beds INTEGER,
    beds_plus INTEGER,
    baths INTEGER,
    sqft INTEGER,
    parking INTEGER,
    fee REAL,
    taxes REAL,
    mls TEXT,
    PRIMARY KEY (building, unit, sold_date)
) WITHOUT ROWID;

-- Small facts the app keeps about itself, e.g. which page-reading version filled the data
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Every table whose rows belong to one listing, as (table, column), children before `listings`
_LISTING_CHILDREN = (
    ("snapshots", "listing_id"),
    ("listing_images", "listing_id"),
    ("price_events", "listing_id"),
    ("past_sales", "listing_id"),
    ("comparables", "listing_id"),
    ("comparables", "favorite_id"),
)

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
                current = 3
            if current < 4:
                _migrate_v3_to_v4(conn)
                conn.execute("UPDATE schema_version SET version = 4")
                conn.commit()
                logger.info("Migrated database to version 4")
                current = 4
            if current < 5:
                _migrate_v4_to_v5(conn)
                conn.execute("UPDATE schema_version SET version = 5")
                conn.commit()
                logger.info("Migrated database to version 5")
                current = 5
            if current < 6:
                _migrate_v5_to_v6(conn)
                conn.execute("UPDATE schema_version SET version = 6")
                conn.commit()
                logger.info("Migrated database to version 6")


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


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS listing_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL REFERENCES listings(id),
            url TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            local_path TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            UNIQUE(listing_id, url)
        );
        CREATE INDEX IF NOT EXISTS idx_listing_images_listing
            ON listing_images(listing_id, position);
    """)
    conn.commit()


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(listings)").fetchall()}
    if "favorited" not in existing:
        conn.execute("ALTER TABLE listings ADD COLUMN favorited INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS comparables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            favorite_id INTEGER NOT NULL REFERENCES listings(id),
            listing_id INTEGER NOT NULL REFERENCES listings(id),
            similarity_score REAL NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_checked TEXT,
            predicted_target_date TEXT,
            price_trend_pct_per_month REAL,
            notes TEXT NOT NULL DEFAULT '',
            UNIQUE(favorite_id, listing_id)
        );
        CREATE INDEX IF NOT EXISTS idx_comparables_favorite ON comparables(favorite_id);
    """)
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
            "building_name=?, city=?, neighbourhood=?, active=?, favorited=?, user_notes=? "
            "WHERE id=?",
            (
                listing.mls_number,
                listing.address,
                listing.unit,
                listing.building_name,
                listing.city,
                listing.neighbourhood,
                1 if listing.active else 0,
                1 if listing.favorited else 0,
                listing.user_notes,
                listing.id,
            ),
        )
        conn.commit()


def toggle_favorite(listing_id: int) -> bool:
    """Toggle the favorited flag. Returns the new state."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT favorited FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        if not row:
            return False
        new_val = 0 if row["favorited"] else 1
        conn.execute(
            "UPDATE listings SET favorited = ? WHERE id = ?", (new_val, listing_id)
        )
        conn.commit()
        return bool(new_val)


def get_favorited_listings() -> list[Listing]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM listings WHERE favorited = 1 AND active = 1"
        ).fetchall()
        return [_row_to_listing(r) for r in rows]


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


def _delete_listings(conn: sqlite3.Connection, ids: list[int]) -> None:
    """Delete listings and every row that belongs to them (the caller commits)."""
    if not ids:
        return
    ph = ",".join("?" * len(ids))
    for table, column in _LISTING_CHILDREN:
        conn.execute(f"DELETE FROM {table} WHERE {column} IN ({ph})", ids)
    conn.execute(f"DELETE FROM listings WHERE id IN ({ph})", ids)


def delete_listing(listing_id: int) -> None:
    with _connect() as conn:
        _delete_listings(conn, [listing_id])
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
        _delete_listings(conn, ids)
        conn.commit()
        return len(ids)


def cleanup_bad_listings(after_fetch: bool = False) -> int:
    """Delete listings that have never had useful data, and deduplicate.

    Only removes listings that have NEVER had a good snapshot (valid address
    or price). Listings that previously parsed successfully are always kept
    even if the latest fetch failed or was blocked.

    Pass after_fetch=True to skip the 2-hour grace period.
    """
    with _connect() as conn:
        ids_to_delete = set()

        grace = "" if after_fetch else (
            " AND l.date_added < '" +
            (datetime.utcnow() - timedelta(hours=2)).isoformat() + "'"
        )

        # Listings with no address AND no snapshot with a valid price — never useful
        rows = conn.execute(
            "SELECT l.id FROM listings l "
            "WHERE (l.address IS NULL OR l.address = '')" + grace + " "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM snapshots s WHERE s.listing_id = l.id "
            "  AND s.list_price IS NOT NULL AND s.list_price >= 10000"
            ")"
        ).fetchall()
        ids_to_delete.update(r["id"] for r in rows)

        # Listings with no snapshots at all (and past grace period)
        rows = conn.execute(
            "SELECT l.id FROM listings l "
            "LEFT JOIN snapshots s ON l.id = s.listing_id "
            "WHERE s.id IS NULL" + grace
        ).fetchall()
        ids_to_delete.update(r["id"] for r in rows)

        # Anomalous prices: delete the BAD SNAPSHOTS, not the listing
        conn.execute(
            "DELETE FROM snapshots WHERE list_price > 0 AND list_price < 10000"
        )

        # Deduplicate: same normalized URL, keep the oldest (lowest id)
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
        _delete_listings(conn, ids)
        # Normalize URLs on remaining listings
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
    """Delete freshly-discovered listings that a buyer can't purchase.

    Only targets listings with exactly one snapshot whose ONLY snapshot is
    SOLD, TERMINATED, or has no price. Never deletes listings with BLOCKED
    or UNKNOWN status (those may just be a temporary fetch failure).
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT l.id FROM listings l "
            "JOIN snapshots s ON s.listing_id = l.id "
            "GROUP BY l.id "
            "HAVING COUNT(s.id) = 1 "
            "AND ("
            "  MAX(s.status) IN ('SOLD', 'TERMINATED')"
            "  OR (MAX(COALESCE(s.list_price, 0)) <= 0 "
            "      AND MAX(s.status) NOT IN ('BLOCKED', 'UNKNOWN'))"
            ")"
        ).fetchall()
        ids = [r["id"] for r in rows]
        _delete_listings(conn, ids)
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


def upsert_comparable(
    favorite_id: int, listing_id: int, similarity_score: float,
    predicted_target_date: Optional[str] = None,
    price_trend_pct_per_month: Optional[float] = None,
    notes: str = "",
) -> int:
    with _connect() as conn:
        now = datetime.utcnow().isoformat()
        cursor = conn.execute(
            "INSERT INTO comparables "
            "(favorite_id, listing_id, similarity_score, first_seen, last_checked, "
            "predicted_target_date, price_trend_pct_per_month, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(favorite_id, listing_id) DO UPDATE SET "
            "similarity_score = excluded.similarity_score, "
            "last_checked = excluded.last_checked, "
            "predicted_target_date = excluded.predicted_target_date, "
            "price_trend_pct_per_month = excluded.price_trend_pct_per_month, "
            "notes = excluded.notes",
            (favorite_id, listing_id, similarity_score, now, now,
             predicted_target_date, price_trend_pct_per_month, notes),
        )
        conn.commit()
        return cursor.lastrowid


def get_comparables_for_favorite(favorite_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT c.*, l.address, l.city, l.url, l.site "
            "FROM comparables c "
            "JOIN listings l ON l.id = c.listing_id "
            "WHERE c.favorite_id = ? AND l.active = 1 "
            "ORDER BY c.similarity_score DESC",
            (favorite_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_comparables_for_favorite(favorite_id: int) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM comparables WHERE favorite_id = ?", (favorite_id,)
        )
        conn.commit()
        return cursor.rowcount


def delete_stale_comparables() -> int:
    """Remove comparables where the favorite is no longer favorited or the comp is inactive."""
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM comparables WHERE "
            "favorite_id NOT IN (SELECT id FROM listings WHERE favorited = 1) "
            "OR listing_id NOT IN (SELECT id FROM listings WHERE active = 1)"
        )
        conn.commit()
        return cursor.rowcount


def _row_to_listing(row: sqlite3.Row) -> Listing:
    keys = row.keys()
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
        favorited=bool(row["favorited"]) if "favorited" in keys else False,
        user_notes=row["user_notes"],
    )


def save_listing_images(listing_id: int, urls: list[str], source: str = "") -> None:
    if not urls:
        return
    with _connect() as conn:
        for i, url in enumerate(urls):
            conn.execute(
                "INSERT OR IGNORE INTO listing_images "
                "(listing_id, url, source, position) VALUES (?, ?, ?, ?)",
                (listing_id, url, source, i),
            )
        conn.commit()


def get_listing_images(listing_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, url, source, local_path, position "
            "FROM listing_images WHERE listing_id = ? ORDER BY position",
            (listing_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_first_image_urls() -> dict[int, str]:
    """{listing_id: url of its first image} for every listing that has images, in one query."""
    with _connect() as conn:
        # SQLite takes the bare `url` column from the row that holds MIN(position)
        rows = conn.execute(
            "SELECT listing_id, url, MIN(position) FROM listing_images GROUP BY listing_id"
        ).fetchall()
        return {r["listing_id"]: r["url"] for r in rows}


PriceHistory = tuple[Optional[str], list[tuple[str, float]]]  # (list date, [(date, price)] oldest first)


def save_price_history(listing_id: int, list_date: Optional[str], points: list[tuple[str, float]]) -> None:
    """Replace a listing's site-reported history; the site's latest record is the authority.
    A page that reported nothing leaves the stored history alone."""
    if not list_date and not points:
        return
    rows = [(listing_id, "price", d, p) for d, p in points]
    if list_date:
        rows.append((listing_id, "listed", list_date, None))
    with _connect() as conn:
        conn.execute("DELETE FROM price_events WHERE listing_id = ?", (listing_id,))
        conn.executemany(
            "INSERT OR REPLACE INTO price_events (listing_id, kind, event_date, price) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()


def get_price_histories(listing_id: Optional[int] = None) -> dict[int, PriceHistory]:
    """{listing_id: (list date, [(date, price)])} for one listing, or for all when none is given."""
    where, args = ("WHERE listing_id = ?", (listing_id,)) if listing_id is not None else ("", ())
    out: dict[int, PriceHistory] = {}
    with _connect() as conn:
        for r in conn.execute(
            f"SELECT listing_id, kind, event_date, price FROM price_events {where} "
            "ORDER BY listing_id, event_date", args,
        ):
            list_date, points = out.setdefault(r["listing_id"], (None, []))
            if r["kind"] == "listed":
                out[r["listing_id"]] = (r["event_date"], points)
            else:
                points.append((r["event_date"], r["price"]))
    return out


def get_snapshot_ids() -> list[tuple[int, int, str]]:
    """(snapshot id, listing id, listing url) for every snapshot, oldest first."""
    with _connect() as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT s.id, s.listing_id, l.url FROM snapshots s "
            "JOIN listings l ON l.id = s.listing_id ORDER BY s.id"
        )]


def save_past_sales(listing_id: int, sales: list[tuple[str, float]]) -> None:
    """Replace a listing's earlier sales with what its page reported (possibly none)."""
    with _connect() as conn:
        conn.execute("DELETE FROM past_sales WHERE listing_id = ?", (listing_id,))
        conn.executemany(
            "INSERT OR REPLACE INTO past_sales (listing_id, sold_date, sold_price) VALUES (?, ?, ?)",
            [(listing_id, d, p) for d, p in sales],
        )
        conn.commit()


def get_past_sales(listing_id: Optional[int] = None) -> dict[int, list[tuple[str, float]]]:
    """{listing_id: [(date, price)] oldest first} for one listing, or for all when none is given."""
    where, args = ("WHERE listing_id = ?", (listing_id,)) if listing_id is not None else ("", ())
    out: dict[int, list[tuple[str, float]]] = {}
    with _connect() as conn:
        for r in conn.execute(
            f"SELECT listing_id, sold_date, sold_price FROM past_sales {where} "
            "ORDER BY listing_id, sold_date", args,
        ):
            out.setdefault(r["listing_id"], []).append((r["sold_date"], r["sold_price"]))
    return out


AREA_SALE_FIELDS = ("sale_key", "sold_date", "sold_price", "city", "address", "type", "beds", "sqft")


def save_area_sales(sales: list[dict]) -> None:
    """Add area sales; one seen again (same key) is updated with the newer details."""
    if not sales:
        return
    with _connect() as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO area_sales ({', '.join(AREA_SALE_FIELDS)}) "
            f"VALUES ({', '.join('?' * len(AREA_SALE_FIELDS))})",
            [tuple(s.get(f) for f in AREA_SALE_FIELDS) for s in sales],
        )
        conn.commit()


def get_area_sales() -> list[dict]:
    with _connect() as conn:
        return [dict(r) for r in conn.execute(f"SELECT {', '.join(AREA_SALE_FIELDS)} FROM area_sales")]


BUILDING_SALE_FIELDS = ("building", "unit", "sold_date", "sold_price", "address", "list_price", "list_date",
                        "beds", "beds_plus", "baths", "sqft", "parking", "fee", "taxes", "mls")


def save_building_sales(sales: list[dict]) -> None:
    if not sales:
        return
    with _connect() as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO building_sales ({', '.join(BUILDING_SALE_FIELDS)}) "
            f"VALUES ({', '.join('?' * len(BUILDING_SALE_FIELDS))})",
            [tuple(s.get(f) for f in BUILDING_SALE_FIELDS) for s in sales],
        )
        conn.commit()


def get_building_sales(building: Optional[str] = None) -> list[dict]:
    """Sales of one building (or all), newest first."""
    where, args = ("WHERE building = ?", (building,)) if building else ("", ())
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT {', '.join(BUILDING_SALE_FIELDS)} FROM building_sales {where} "
            "ORDER BY sold_date DESC", args)]


def fill_missing_property_type(listing_id: int, property_type: str) -> None:
    """Set the latest snapshot's property type if it has none."""
    with _connect() as conn:
        conn.execute(
            "UPDATE snapshots SET property_type = ? WHERE id = "
            "(SELECT MAX(id) FROM snapshots WHERE listing_id = ?) AND property_type IS NULL",
            (property_type, listing_id),
        )
        conn.commit()


def get_state(key: str) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_state(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def update_image_local_path(image_id: int, local_path: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE listing_images SET local_path = ? WHERE id = ?",
            (local_path, image_id),
        )
        conn.commit()


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

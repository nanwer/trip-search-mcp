"""SQLite-backed watch storage for the deal-hunting tools.

Schema is intentionally flat — one `watches` table — because watches are
the only persistent state this server has. Each watch records a flight
query, a price threshold, and the latest price observation.

The DB lives at `~/.trip-search-mcp/watches.db`. Path is overridable
via the `TRIP_SEARCH_DB_PATH` env var (used in tests to point at a
temp file).

Concurrency: SQLite handles this fine for our scale. All connections
open with `check_same_thread=False` so the async scheduler and the
tool-function call paths can share the file. Use one connection per
operation; don't hold it open.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    watch_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    return_date TEXT,
    threshold_price REAL NOT NULL,
    currency TEXT NOT NULL,
    adults INTEGER NOT NULL DEFAULT 1,
    cabin_class TEXT NOT NULL DEFAULT 'ECONOMY',
    max_stops TEXT NOT NULL DEFAULT 'ANY',
    status TEXT NOT NULL DEFAULT 'active',
    last_checked_at TEXT,
    last_price REAL,
    last_currency TEXT,
    last_offer_id TEXT,
    alerted_at TEXT,
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_watches_status ON watches(status);
CREATE INDEX IF NOT EXISTS idx_watches_last_checked ON watches(last_checked_at);
"""


def db_path() -> Path:
    """Resolved DB path. Honors TRIP_SEARCH_DB_PATH for tests."""
    override = os.environ.get("TRIP_SEARCH_DB_PATH")
    if override:
        return Path(override)
    return Path.home() / ".trip-search-mcp" / "watches.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db() -> None:
    """Create tables if they don't exist. Idempotent — safe to call at
    every server start."""
    with connect() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ----- CRUD ----------------------------------------------------------------


def create_watch(
    *,
    origin: str,
    destination: str,
    departure_date: str,
    threshold_price: float,
    currency: str,
    return_date: str | None = None,
    adults: int = 1,
    cabin_class: str = "ECONOMY",
    max_stops: str = "ANY",
    note: str | None = None,
) -> str:
    """Insert a new watch row, return the generated watch_id."""
    watch_id = uuid.uuid4().hex[:12]  # 12 hex chars is comfortable for humans
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO watches (
                watch_id, created_at, origin, destination,
                departure_date, return_date,
                threshold_price, currency, adults, cabin_class, max_stops,
                status, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                watch_id, _now_iso(), origin, destination,
                departure_date, return_date,
                threshold_price, currency, adults, cabin_class, max_stops,
                note,
            ),
        )
        conn.commit()
    return watch_id


def get_watch(watch_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM watches WHERE watch_id = ?", (watch_id,)
        ).fetchone()
        return dict(row) if row else None


def list_watches(*, status: str | None = "active") -> list[dict[str, Any]]:
    """List watches. status=None returns all (active + cancelled + alerted)."""
    with connect() as conn:
        if status is None:
            rows = conn.execute(
                "SELECT * FROM watches ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM watches WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
    return [dict(r) for r in rows]


def cancel_watch(watch_id: str) -> bool:
    """Mark a watch as cancelled. Returns True if a row was updated."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE watches SET status = 'cancelled' WHERE watch_id = ? AND status != 'cancelled'",
            (watch_id,),
        )
        conn.commit()
        return cur.rowcount > 0


def record_check(
    watch_id: str,
    *,
    price: float | None,
    currency: str | None,
    offer_id: str | None,
    mark_alerted: bool,
) -> None:
    """Record the result of a freshness check against the upstream
    search. Sets `last_*` columns and flips status → 'alerted' when
    mark_alerted=True."""
    now = _now_iso()
    with connect() as conn:
        if mark_alerted:
            conn.execute(
                """
                UPDATE watches
                SET last_checked_at = ?, last_price = ?, last_currency = ?,
                    last_offer_id = ?, status = 'alerted', alerted_at = ?
                WHERE watch_id = ?
                """,
                (now, price, currency, offer_id, now, watch_id),
            )
        else:
            conn.execute(
                """
                UPDATE watches
                SET last_checked_at = ?, last_price = ?, last_currency = ?,
                    last_offer_id = ?
                WHERE watch_id = ?
                """,
                (now, price, currency, offer_id, watch_id),
            )
        conn.commit()

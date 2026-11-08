"""SQLite storage for PackProof.

Tables mirror the PayPal objects the app works with (orders, disputes)
plus PackProof's own objects: sealed evidence packs, verdicts, and the
log of PayPal API actions taken (real or simulated).
"""
import json
import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS buyers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    item_name TEXT NOT NULL,
    sku TEXT NOT NULL,
    item_color TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    buyer_id TEXT NOT NULL REFERENCES buyers(id),
    tracking_status TEXT NOT NULL DEFAULT 'delivered',
    created_at TEXT NOT NULL,
    delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS packs (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
    photo_path TEXT NOT NULL,
    weight_g INTEGER NOT NULL,
    sealed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS disputes (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    buyer_id TEXT NOT NULL REFERENCES buyers(id),
    reason TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL DEFAULT 'WAITING_FOR_SELLER_RESPONSE',
    claim_photo_path TEXT,
    claim_photo_hash TEXT,
    claim_photo_taken_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dispute_id TEXT NOT NULL REFERENCES disputes(id),
    verdict TEXT NOT NULL,
    score INTEGER,
    path TEXT NOT NULL,
    settle_fraction REAL,
    breakdown TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dispute_id TEXT,
    action TEXT NOT NULL,
    mode TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def row_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def get_dispute_ctx(conn: sqlite3.Connection, dispute_id: str) -> dict | None:
    """Everything the verdict engine and UI need for one dispute."""
    d = conn.execute("SELECT * FROM disputes WHERE id = ?", (dispute_id,)).fetchone()
    if d is None:
        return None
    dispute = dict(d)
    order = row_dict(
        conn.execute("SELECT * FROM orders WHERE id = ?", (dispute["order_id"],)).fetchone()
    )
    pack = row_dict(
        conn.execute("SELECT * FROM packs WHERE order_id = ?", (dispute["order_id"],)).fetchone()
    )
    buyer = row_dict(
        conn.execute("SELECT * FROM buyers WHERE id = ?", (dispute["buyer_id"],)).fetchone()
    )
    prior = conn.execute(
        "SELECT COUNT(*) AS n FROM disputes WHERE buyer_id = ? AND id != ?",
        (dispute["buyer_id"], dispute_id),
    ).fetchone()["n"]
    reuse = 0
    if dispute.get("claim_photo_hash"):
        reuse = conn.execute(
            "SELECT COUNT(*) AS n FROM disputes WHERE claim_photo_hash = ? AND id != ?",
            (dispute["claim_photo_hash"], dispute_id),
        ).fetchone()["n"]
    verdict = conn.execute(
        "SELECT * FROM verdicts WHERE dispute_id = ? ORDER BY id DESC LIMIT 1",
        (dispute_id,),
    ).fetchone()
    verdict = row_dict(verdict)
    if verdict is not None:
        verdict["breakdown"] = json.loads(verdict["breakdown"])
    return {
        "dispute": dispute,
        "order": order,
        "pack": pack,
        "buyer": buyer,
        "buyer_prior_disputes": prior,
        "photo_reuse_count": reuse,
        "verdict": verdict,
    }


def log_action(conn: sqlite3.Connection, dispute_id: str | None, action: str,
               mode: str, detail: dict, created_at: str) -> None:
    conn.execute(
        "INSERT INTO actions (dispute_id, action, mode, detail, created_at) VALUES (?,?,?,?,?)",
        (dispute_id, action, mode, json.dumps(detail), created_at),
    )
    conn.commit()

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    chain TEXT NOT NULL CHECK(chain IN ('solana', 'sui')),
    ref_type TEXT NOT NULL CHECK(ref_type IN ('contract', 'id')),
    token_ref TEXT NOT NULL,
    label TEXT,
    threshold_pct REAL NOT NULL,
    baseline_price REAL NOT NULL,
    last_price REAL,
    last_checked_at TEXT,
    created_at TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def add_watch(
    chat_id: int,
    chain: str,
    ref_type: str,
    token_ref: str,
    threshold_pct: float,
    baseline_price: float,
    label: Optional[str] = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO watches
                (chat_id, chain, ref_type, token_ref, label, threshold_pct,
                 baseline_price, last_price, last_checked_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                chain,
                ref_type,
                token_ref,
                label,
                threshold_pct,
                baseline_price,
                baseline_price,
                now,
                now,
            ),
        )
        return cur.lastrowid


def remove_watch(chat_id: int, watch_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM watches WHERE id = ? AND chat_id = ?", (watch_id, chat_id)
        )
        return cur.rowcount > 0


def list_watches(chat_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM watches WHERE chat_id = ? ORDER BY id", (chat_id,)
        ).fetchall()


def all_watches() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM watches").fetchall()


def update_after_check(watch_id: int, last_price: float, new_baseline: Optional[float] = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        if new_baseline is not None:
            conn.execute(
                "UPDATE watches SET last_price = ?, baseline_price = ?, last_checked_at = ? WHERE id = ?",
                (last_price, new_baseline, now, watch_id),
            )
        else:
            conn.execute(
                "UPDATE watches SET last_price = ?, last_checked_at = ? WHERE id = ?",
                (last_price, now, watch_id),
            )

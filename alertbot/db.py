import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from . import config

# chain has no CHECK constraint: it's validated in bot.py against the set of
# supported chains, which is expected to grow over time. Baking the list into
# a CHECK would require a schema migration every time a chain is added.
SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    chain TEXT NOT NULL,
    ref_type TEXT NOT NULL CHECK(ref_type IN ('contract', 'id')),
    token_ref TEXT NOT NULL,
    label TEXT,
    threshold_pct REAL NOT NULL,
    baseline_price REAL NOT NULL,
    last_price REAL,
    last_checked_at TEXT,
    created_at TEXT NOT NULL,
    interval_minutes REAL
);

-- Per-chat settings (e.g. each chat's own default poll interval). Keyed by
-- (chat_id, key) so one chat's setting can never leak into another chat.
CREATE TABLE IF NOT EXISTS settings (
    chat_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (chat_id, key)
);
"""


def _migrate_watches_chain_check(conn: sqlite3.Connection) -> None:
    """Older databases created `watches.chain` with `CHECK(chain IN ('solana',
    'sui'))`, which would reject rows for newly-added chains. Rebuild the
    table without that constraint if it's still present."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'watches'"
    ).fetchone()
    if row is None or "CHECK(chain IN" not in row["sql"]:
        return

    conn.execute("ALTER TABLE watches RENAME TO watches_pre_chain_migration")
    conn.executescript(SCHEMA)
    conn.execute(
        """
        INSERT INTO watches (id, chat_id, chain, ref_type, token_ref, label,
                              threshold_pct, baseline_price, last_price,
                              last_checked_at, created_at)
        SELECT id, chat_id, chain, ref_type, token_ref, label,
               threshold_pct, baseline_price, last_price,
               last_checked_at, created_at
        FROM watches_pre_chain_migration
        """
    )
    conn.execute("DROP TABLE watches_pre_chain_migration")


def _migrate_add_interval_column(conn: sqlite3.Connection) -> None:
    """Databases created before per-watch polling intervals existed are
    missing this column; add it (NULL = use the global default interval)."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(watches)").fetchall()}
    if "interval_minutes" not in cols:
        conn.execute("ALTER TABLE watches ADD COLUMN interval_minutes REAL")


def _migrate_settings_chat_scope(conn: sqlite3.Connection) -> None:
    """Older databases had a single global `settings` row with no chat_id,
    meaning one chat's /setinterval silently changed the default for every
    chat using the bot. Rebuild the table scoped by chat_id. There's no way
    to attribute the old global value to a specific chat, so it's dropped;
    chats fall back to POLL_INTERVAL_MINUTES until they set their own again."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(settings)").fetchall()}
    if "chat_id" in cols:
        return
    conn.execute("DROP TABLE IF EXISTS settings")
    conn.executescript(SCHEMA)


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
        _migrate_watches_chain_check(conn)
        _migrate_add_interval_column(conn)
        _migrate_settings_chat_scope(conn)


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


def get_chat_setting(chat_id: int, key: str, default: Optional[str] = None) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE chat_id = ? AND key = ?", (chat_id, key)
        ).fetchone()
        return row["value"] if row else default


def set_chat_setting(chat_id: int, key: str, value) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (chat_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, key) DO UPDATE SET value = excluded.value",
            (chat_id, key, str(value)),
        )


def all_chat_settings(key: str) -> dict:
    """Map of chat_id -> value (as stored) for every chat that has set this key."""
    with get_conn() as conn:
        rows = conn.execute("SELECT chat_id, value FROM settings WHERE key = ?", (key,)).fetchall()
        return {row["chat_id"]: row["value"] for row in rows}


def min_chat_setting(key: str) -> Optional[float]:
    """Smallest value set for this key across all chats, or None if no chat
    has set it (i.e. everyone is still on the config-file default)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MIN(CAST(value AS REAL)) AS m FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["m"] if row and row["m"] is not None else None


def set_watch_interval(chat_id: int, watch_id: int, minutes: Optional[float]) -> bool:
    """Set (or, with minutes=None, clear) a watch's own polling interval.
    Returns False if no such watch exists for this chat."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE watches SET interval_minutes = ? WHERE id = ? AND chat_id = ?",
            (minutes, watch_id, chat_id),
        )
        return cur.rowcount > 0


def min_watch_interval() -> Optional[float]:
    """Smallest per-watch interval currently set (ignoring watches that use
    the global default), or None if none have a custom interval."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MIN(interval_minutes) AS m FROM watches WHERE interval_minutes IS NOT NULL"
        ).fetchone()
        return row["m"] if row and row["m"] is not None else None


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

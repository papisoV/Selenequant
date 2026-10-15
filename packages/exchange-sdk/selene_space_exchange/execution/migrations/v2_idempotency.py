"""v2 idempotency migration — adds UNIQUE constraint on exchange_order_id.

SQLite cannot ALTER TABLE to add a UNIQUE constraint to an existing column.
We use the three-step table-rebuild pattern documented by SQLite官方:

    1. CREATE TABLE order_journal_new (with exchange_order_id TEXT UNIQUE)
    2. INSERT INTO order_journal_new SELECT * FROM order_journal
    3. DROP TABLE order_journal
    4. ALTER TABLE order_journal_new RENAME TO order_journal
    5. Recreate indexes (dropped with old table)

Idempotent: detects existing UNIQUE constraint via PRAGMA table_info +
sqlite_master index inspection, skips rebuild if already present.

Run order: OrderJournal._init_db calls this automatically on startup,
after v1_latency_columns migration.
"""

from __future__ import annotations

import sqlite3
import logging

logger = logging.getLogger(__name__)


def _has_unique_on_exchange_order_id(con: sqlite3.Connection) -> bool:
    """Detect whether exchange_order_id already has a UNIQUE constraint.

    SQLite stores UNIQUE constraints as auto-indexes named `sqlite_autoindex_*`
    or with explicit `CREATE UNIQUE INDEX` entries in sqlite_master.
    We inspect both: PRAGMA table_info shows `pk`/not-nullable but not UNIQUE,
    so we check sqlite_master for unique indexes referencing the column.
    """
    cur = con.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type='table' AND name='order_journal'
        """
    )
    row = cur.fetchone()
    if row and row[0] and "EXCHANGE_ORDER_ID TEXT UNIQUE" in row[0].upper():
        return True

    cur = con.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type='index' AND tbl_name='order_journal'
        """
    )
    for (sql,) in cur.fetchall():
        if not sql:
            continue
        if "UNIQUE" in sql.upper() and "EXCHANGE_ORDER_ID" in sql.upper():
            return True
    return False


def migrate(con: sqlite3.Connection) -> None:
    """Add UNIQUE constraint to exchange_order_id via three-step rebuild.

    Args:
        con: Open sqlite3 connection. Caller's context commits.
    """
    if _has_unique_on_exchange_order_id(con):
        return

    logger.info("order_journal: rebuilding table to add UNIQUE on exchange_order_id")

    con.executescript(
        """
        PRAGMA foreign_keys=OFF;
        BEGIN;
        """
    )
    try:
        con.execute(
            """
            CREATE TABLE order_journal_new (
                journal_id TEXT PRIMARY KEY,
                client_order_id TEXT NOT NULL UNIQUE,
                exchange_order_id TEXT UNIQUE,
                account_id TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL DEFAULT '',
                side TEXT NOT NULL DEFAULT '',
                order_type TEXT NOT NULL DEFAULT '',
                quantity REAL NOT NULL DEFAULT 0,
                price REAL,
                status TEXT NOT NULL DEFAULT 'intent',
                reason TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL DEFAULT 0,
                raw_request TEXT,
                submitted_at INTEGER,
                acknowledged_at INTEGER,
                filled_at INTEGER
            )
            """
        )
        con.execute(
            """
            INSERT INTO order_journal_new
                (journal_id, client_order_id, exchange_order_id, account_id, symbol,
                 side, order_type, quantity, price, status, reason,
                 created_at, updated_at, raw_request,
                 submitted_at, acknowledged_at, filled_at)
            SELECT
                journal_id, client_order_id,
                COALESCE(NULLIF(exchange_order_id, ''), NULL),
                account_id, symbol, side, order_type, quantity, price, status, reason,
                created_at, updated_at, raw_request,
                submitted_at, acknowledged_at, filled_at
            FROM order_journal
            """
        )
        con.execute("DROP TABLE order_journal")
        con.execute("ALTER TABLE order_journal_new RENAME TO order_journal")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_journal_status ON order_journal (status)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_journal_client_oid ON order_journal (client_order_id)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_journal_exchange_oid ON order_journal (exchange_order_id)"
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.execute("PRAGMA foreign_keys=ON")

    logger.info("order_journal: UNIQUE constraint on exchange_order_id added")

"""v1 latency columns migration — adds submitted_at, acknowledged_at, filled_at.

Idempotent: detects existing columns via PRAGMA table_info and only ALTERs when
missing. Backfills historical rows from `updated_at` so old data is queryable.

Run order: OrderJournal._init_db calls this automatically on startup.
"""

from __future__ import annotations

import sqlite3
import logging

logger = logging.getLogger(__name__)


def migrate(con: sqlite3.Connection) -> None:
    """Add latency columns to order_journal if missing. Backfills from updated_at.

    Args:
        con: Open sqlite3 connection (will be committed by the caller's context).
    """
    cols = {row[1] for row in con.execute("PRAGMA table_info(order_journal)")}
    added: list[str] = []

    if "submitted_at" not in cols:
        con.execute("ALTER TABLE order_journal ADD COLUMN submitted_at INTEGER")
        added.append("submitted_at")
    if "acknowledged_at" not in cols:
        con.execute("ALTER TABLE order_journal ADD COLUMN acknowledged_at INTEGER")
        added.append("acknowledged_at")
    if "filled_at" not in cols:
        con.execute("ALTER TABLE order_journal ADD COLUMN filled_at INTEGER")
        added.append("filled_at")

    if not added:
        return

    logger.info("order_journal: added columns %s", added)

    # Backfill historical rows from updated_at (best-effort).
    # submitted_at for rows already past 'intent' state:
    con.execute(
        """
        UPDATE order_journal
        SET submitted_at = updated_at
        WHERE submitted_at IS NULL
          AND status IN ('submitted', 'confirmed', 'failed', 'expired')
        """
    )
    # acknowledged_at for confirmed rows:
    con.execute(
        """
        UPDATE order_journal
        SET acknowledged_at = updated_at
        WHERE acknowledged_at IS NULL
          AND status = 'confirmed'
        """
    )
    # filled_at for confirmed rows (treat confirmed == filled for legacy rows):
    con.execute(
        """
        UPDATE order_journal
        SET filled_at = updated_at
        WHERE filled_at IS NULL
          AND status = 'confirmed'
        """
    )

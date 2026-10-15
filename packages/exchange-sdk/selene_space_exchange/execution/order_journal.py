"""Order journal (WAL) for crash-safe order tracking.

Every order intent is written to a SQLite append-only log BEFORE the
REST call reaches the exchange. On crash recovery, pending entries
(INTENT / SUBMITTED) are reconciled against exchange state.

States: INTENT → SUBMITTED → CONFIRMED | FAILED | EXPIRED
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_JOURNAL_DIR = Path.home() / ".selenespace" / "journal"


@dataclass
class JournalEntry:
    """A single order journal entry."""

    journal_id: str
    client_order_id: str
    exchange_order_id: str | None = None
    account_id: str = ""
    symbol: str = ""
    side: str = ""
    order_type: str = ""
    quantity: float = 0.0
    price: float | None = None
    status: str = "intent"  # intent, submitted, confirmed, failed, expired
    reason: str = ""
    created_at: int = 0
    updated_at: int = 0
    raw_request: dict[str, Any] | None = None
    # Latency tracking (Phase 2a): nullable; populated when the
    # corresponding state transition fires.
    submitted_at: int | None = None
    acknowledged_at: int | None = None
    filled_at: int | None = None


class OrderJournal:
    """Append-only order journal for crash recovery.

    Usage::

        journal = OrderJournal()
        jid = await journal.append_intent(
            client_order_id="co-123",
            symbol="BTC-USDT-SWAP",
            side="buy",
            order_type="market",
            quantity=100.0,
            account_id="pa-abc",
        )
        # ... send REST request ...
        await journal.mark_submitted("co-123", exchange_order_id="ex-456")
        # ... WS confirms fill ...
        await journal.mark_confirmed("co-123")
    """

    def __init__(self, journal_dir: Path | None = None) -> None:
        self._dir = journal_dir or _DEFAULT_JOURNAL_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._dir / "order_journal.db"
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS order_journal (
                    journal_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL UNIQUE,
                    exchange_order_id TEXT,
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
                    raw_request TEXT
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_journal_status
                ON order_journal (status)
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_journal_client_oid
                ON order_journal (client_order_id)
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_journal_exchange_oid
                ON order_journal (exchange_order_id)
            """)

        # Phase 2a: idempotent column migration (adds latency columns if missing)
        from selene_space_exchange.execution.migrations.v1_latency_columns import migrate
        with self._connect() as con:
            migrate(con)

        # Phase 2.5: idempotent UNIQUE constraint migration on exchange_order_id
        # (three-step table rebuild: CREATE new + INSERT SELECT + DROP old + RENAME)
        from selene_space_exchange.execution.migrations.v2_idempotency import migrate as _migrate_v2
        with self._connect() as con:
            _migrate_v2(con)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._db_path), timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def append_intent(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        account_id: str = "",
        raw_request: dict[str, Any] | None = None,
    ) -> str:
        """Write order intent to journal. Returns journal entry ID.

        Idempotent: if ``client_order_id`` already exists as an intent, the
        existing journal_id is returned without re-inserting. This lets
        callers retry place_order after a partial failure (e.g. crash between
        append_intent and mark_submitted) without tripping the UNIQUE
        constraint on client_order_id.
        """
        now_ms = int(time.time() * 1000)

        with self._connect() as con:
            existing = con.execute(
                "SELECT journal_id, status FROM order_journal WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            if existing is not None:
                # Already journaled — return the existing id. If the existing
                # row is still 'intent', the caller can proceed to
                # mark_submitted. If it's already submitted/confirmed/failed,
                # the caller's mark_submitted will be a no-op.
                return existing[0]

            journal_id = f"j-{uuid.uuid4().hex[:12]}"
            con.execute(
                """
                INSERT INTO order_journal
                    (journal_id, client_order_id, account_id, symbol, side,
                     order_type, quantity, price, status, created_at, updated_at, raw_request)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'intent', ?, ?, ?)
                """,
                (
                    journal_id,
                    client_order_id,
                    account_id,
                    symbol,
                    side,
                    order_type,
                    quantity,
                    price,
                    now_ms,
                    now_ms,
                    json.dumps(raw_request) if raw_request else None,
                ),
            )

        logger.info(
            "Journal INTENT: %s %s %s %s qty=%.2f",
            client_order_id, side, order_type, symbol, quantity,
        )
        return journal_id

    def mark_submitted(self, client_order_id: str, exchange_order_id: str) -> None:
        """Mark order as submitted to exchange (REST call made)."""
        now_ms = int(time.time() * 1000)
        with self._connect() as con:
            con.execute(
                """
                UPDATE order_journal
                SET status='submitted',
                    exchange_order_id=?,
                    submitted_at=COALESCE(submitted_at, ?),
                    updated_at=?
                WHERE client_order_id=? AND status='intent'
                """,
                (exchange_order_id, now_ms, now_ms, client_order_id),
            )

        logger.info("Journal SUBMITTED: %s → exchange %s", client_order_id, exchange_order_id)

    def mark_confirmed(self, client_order_id: str) -> None:
        """Mark order as confirmed by exchange WS update. Idempotent.

        - status='confirmed' on second call → no-op (idempotent)
        - status='failed' → refuse transition (terminal state)
        - status='expired' → refuse transition (terminal state)
        """
        now_ms = int(time.time() * 1000)
        with self._connect() as con:
            cur = con.execute(
                "SELECT status FROM order_journal WHERE client_order_id=?",
                (client_order_id,),
            )
            row = cur.fetchone()
            if row is None:
                return
            current_status = row[0]
            if current_status == "confirmed":
                return
            if current_status in ("failed", "expired"):
                logger.warning(
                    "Journal mark_confirmed rejected: %s is %s (terminal)",
                    client_order_id, current_status,
                )
                return
            con.execute(
                """
                UPDATE order_journal
                SET status='confirmed',
                    acknowledged_at=COALESCE(acknowledged_at, ?),
                    updated_at=?
                WHERE client_order_id=? AND status IN ('submitted', 'intent')
                """,
                (now_ms, now_ms, client_order_id),
            )

        logger.info("Journal CONFIRMED: %s", client_order_id)

    def mark_filled(self, client_order_id: str, fill_ts: int | None = None) -> None:
        """Mark order as filled (trade completed).

        Args:
            client_order_id: Client order ID.
            fill_ts: Optional fill timestamp (ms epoch). If None, uses now.
        """
        now_ms = fill_ts if fill_ts is not None else int(time.time() * 1000)
        with self._connect() as con:
            con.execute(
                """
                UPDATE order_journal
                SET filled_at=COALESCE(filled_at, ?),
                    updated_at=?
                WHERE client_order_id=?
                """,
                (now_ms, now_ms, client_order_id),
            )

        logger.info("Journal FILLED: %s @ %d", client_order_id, now_ms)

    def mark_failed(self, client_order_id: str, reason: str = "") -> None:
        """Mark order as failed/rejected. Idempotent: status='failed' on second call → no-op."""
        now_ms = int(time.time() * 1000)
        with self._connect() as con:
            cur = con.execute(
                "SELECT status FROM order_journal WHERE client_order_id=?",
                (client_order_id,),
            )
            row = cur.fetchone()
            if row is None:
                return
            current_status = row[0]
            if current_status == "failed":
                return
            if current_status in ("confirmed", "expired"):
                logger.warning(
                    "Journal mark_failed rejected: %s is %s (terminal)",
                    client_order_id, current_status,
                )
                return
            con.execute(
                """
                UPDATE order_journal
                SET status='failed', reason=?, updated_at=?
                WHERE client_order_id=? AND status IN ('intent', 'submitted')
                """,
                (reason, now_ms, client_order_id),
            )

        logger.warning("Journal FAILED: %s — %s", client_order_id, reason)

    def mark_expired(self, client_order_id: str, reason: str = "") -> None:
        """Mark order as expired (e.g. GTD order past validity). Idempotent: status='expired' → no-op."""
        now_ms = int(time.time() * 1000)
        with self._connect() as con:
            cur = con.execute(
                "SELECT status FROM order_journal WHERE client_order_id=?",
                (client_order_id,),
            )
            row = cur.fetchone()
            if row is None:
                return
            current_status = row[0]
            if current_status == "expired":
                return
            if current_status in ("confirmed", "failed"):
                logger.warning(
                    "Journal mark_expired rejected: %s is %s (terminal)",
                    client_order_id, current_status,
                )
                return
            con.execute(
                """
                UPDATE order_journal
                SET status='expired', reason=?, updated_at=?
                WHERE client_order_id=? AND status IN ('intent', 'submitted')
                """,
                (reason, now_ms, client_order_id),
            )

    def get_pending_entries(self) -> list[JournalEntry]:
        """Get all INTENT/SUBMITTED entries for crash recovery."""
        with self._connect() as con:
            cur = con.execute(
                """
                SELECT journal_id, client_order_id, exchange_order_id, account_id,
                       symbol, side, order_type, quantity, price, status, reason,
                       created_at, updated_at, raw_request,
                       submitted_at, acknowledged_at, filled_at
                FROM order_journal
                WHERE status IN ('intent', 'submitted')
                ORDER BY created_at ASC
                """
            )
            rows = cur.fetchall()

        entries = []
        for row in rows:
            entries.append(JournalEntry(
                journal_id=row[0],
                client_order_id=row[1],
                exchange_order_id=row[2],
                account_id=row[3],
                symbol=row[4],
                side=row[5],
                order_type=row[6],
                quantity=row[7],
                price=row[8],
                status=row[9],
                reason=row[10],
                created_at=row[11],
                updated_at=row[12],
                raw_request=json.loads(row[13]) if row[13] else None,
                submitted_at=row[14],
                acknowledged_at=row[15],
                filled_at=row[16],
            ))
        return entries

    def get_pending_count(self) -> int:
        """Count INTENT/SUBMITTED entries — Phase 5 observability: WAL lag indicator."""
        with self._connect() as con:
            cur = con.execute(
                "SELECT COUNT(*) FROM order_journal WHERE status IN ('intent', 'submitted')"
            )
            return int(cur.fetchone()[0])

    def get_entry(self, client_order_id: str) -> JournalEntry | None:
        """Get a single journal entry by client_order_id."""
        with self._connect() as con:
            cur = con.execute(
                """
                SELECT journal_id, client_order_id, exchange_order_id, account_id,
                       symbol, side, order_type, quantity, price, status, reason,
                       created_at, updated_at, raw_request,
                       submitted_at, acknowledged_at, filled_at
                FROM order_journal
                WHERE client_order_id=?
                """,
                (client_order_id,),
            )
            row = cur.fetchone()

        if row is None:
            return None
        return JournalEntry(
            journal_id=row[0],
            client_order_id=row[1],
            exchange_order_id=row[2],
            account_id=row[3],
            symbol=row[4],
            side=row[5],
            order_type=row[6],
            quantity=row[7],
            price=row[8],
            status=row[9],
            reason=row[10],
            created_at=row[11],
            updated_at=row[12],
            raw_request=json.loads(row[13]) if row[13] else None,
            submitted_at=row[14],
            acknowledged_at=row[15],
            filled_at=row[16],
        )

    def cleanup_old_entries(self, max_age_days: int = 30) -> int:
        """Remove confirmed/failed/expired entries older than max_age_days."""
        cutoff_ms = int((time.time() - max_age_days * 86400) * 1000)
        with self._connect() as con:
            cur = con.execute(
                """
                DELETE FROM order_journal
                WHERE status IN ('confirmed', 'failed', 'expired')
                  AND updated_at < ?
                """,
                (cutoff_ms,),
            )
            deleted = cur.rowcount
        if deleted > 0:
            logger.info("Journal cleanup: removed %d old entries", deleted)
        return deleted

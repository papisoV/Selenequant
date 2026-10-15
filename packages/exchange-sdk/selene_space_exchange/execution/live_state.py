"""Persistent state storage for LiveExecutionEngine.

Survives process crashes by writing orders, fills, positions, balances,
and ID mappings to SQLite. On restart, StartupReconciler uses this
to rebuild in-memory state.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from selene_space_types.trading import BalanceUpdate, Fill, Order, Position

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = Path.home() / ".selenespace" / "live"


class LiveStateRepository:
    """Persistent storage for LiveExecutionEngine state.

    Usage::

        repo = LiveStateRepository()
        await repo.save_order(order, client_order_id="co-123", exchange_order_id="ex-456")
        orders = await repo.load_orders()
        mappings = await repo.load_id_mappings()
    """

    def __init__(self, state_dir: Path | None = None) -> None:
        self._dir = state_dir or _DEFAULT_STATE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._dir / "live_state.db"
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS live_orders (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    client_order_id TEXT,
                    exchange_order_id TEXT,
                    updated_at INTEGER NOT NULL
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_lo_client_oid
                ON live_orders (client_order_id)
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_lo_exchange_oid
                ON live_orders (exchange_order_id)
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS live_fills (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    timestamp INTEGER NOT NULL
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_lf_order_id
                ON live_fills (order_id)
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS live_positions (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS live_balances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    data TEXT NOT NULL,
                    timestamp INTEGER NOT NULL
                )
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS id_mappings (
                    internal_id TEXT PRIMARY KEY,
                    client_order_id TEXT,
                    exchange_order_id TEXT
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_im_client_oid
                ON id_mappings (client_order_id)
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_im_exchange_oid
                ON id_mappings (exchange_order_id)
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS equity_peak (
                    account_id TEXT PRIMARY KEY,
                    peak REAL NOT NULL,
                    peak_date TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
            """)

            # Shadow-vs-paper equity comparison points (Stage B criterion 5).
            # Written after each shadow fill so verdict.py (separate process)
            # can verify |shadow − paper| / paper < 5% across the whole run.
            con.execute("""
                CREATE TABLE IF NOT EXISTS equity_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shadow_equity REAL NOT NULL,
                    paper_mirror_equity REAL NOT NULL,
                    deviation_pct REAL,
                    timestamp INTEGER NOT NULL
                )
            """)

            # Bug C: StopManager active stops for crash recovery.
            # Serialized as JSON (dataclasses.asdict). StopSide/StopStatus
            # are str-Enum so they round-trip via StopSide(value).
            con.execute("""
                CREATE TABLE IF NOT EXISTS live_stops (
                    stop_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._db_path), timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    # -- Orders --

    def save_order(
        self,
        order: Order,
        *,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> None:
        now_ms = int(time.time() * 1000)
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO live_orders (id, data, client_order_id, exchange_order_id, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (order.id, order.model_dump_json(), client_order_id, exchange_order_id, now_ms),
            )

    def load_orders(self) -> dict[str, Order]:
        with self._connect() as con:
            cur = con.execute("SELECT id, data FROM live_orders")
            rows = cur.fetchall()

        orders = {}
        for row in rows:
            try:
                orders[row[0]] = Order.model_validate_json(row[1])
            except Exception:
                logger.warning("Failed to parse persisted order %s", row[0])
        return orders

    def delete_order(self, order_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM live_orders WHERE id=?", (order_id,))

    # -- Fills --

    def save_fill(self, fill: Fill) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO live_fills (id, data, order_id, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (fill.id, fill.model_dump_json(), fill.order_id, fill.timestamp),
            )

    def load_fills(self) -> list[Fill]:
        with self._connect() as con:
            cur = con.execute("SELECT id, data FROM live_fills ORDER BY timestamp ASC")
            rows = cur.fetchall()

        fills = []
        for row in rows:
            try:
                fills.append(Fill.model_validate_json(row[1]))
            except Exception:
                logger.warning("Failed to parse persisted fill %s", row[0])
        return fills

    # -- Positions --

    def save_positions(self, positions: list[Position]) -> None:
        now_ms = int(time.time() * 1000)
        with self._connect() as con:
            con.execute("DELETE FROM live_positions")
            for pos in positions:
                key = f"{pos.symbol}:{pos.pos_side.value}"
                con.execute(
                    "INSERT INTO live_positions (key, data, updated_at) VALUES (?, ?, ?)",
                    (key, pos.model_dump_json(), now_ms),
                )

    def load_positions(self) -> list[Position]:
        with self._connect() as con:
            cur = con.execute("SELECT key, data FROM live_positions")
            rows = cur.fetchall()

        positions = []
        for row in rows:
            try:
                positions.append(Position.model_validate_json(row[1]))
            except Exception:
                logger.warning("Failed to parse persisted position %s", row[0])
        return positions

    # -- Balances --

    def save_balances(self, balances: list[BalanceUpdate]) -> None:
        now_ms = int(time.time() * 1000)
        with self._connect() as con:
            con.execute("DELETE FROM live_balances")
            for bal in balances:
                con.execute(
                    "INSERT INTO live_balances (data, timestamp) VALUES (?, ?)",
                    (bal.model_dump_json(), now_ms),
                )

    def load_balances(self) -> list[BalanceUpdate]:
        with self._connect() as con:
            cur = con.execute("SELECT data FROM live_balances")
            rows = cur.fetchall()

        balances = []
        for row in rows:
            try:
                balances.append(BalanceUpdate.model_validate_json(row[0]))
            except Exception:
                logger.warning("Failed to parse persisted balance")
        return balances

    # -- ID Mappings --

    def save_id_mapping(
        self,
        internal_id: str,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO id_mappings (internal_id, client_order_id, exchange_order_id)
                VALUES (?, ?, ?)
                """,
                (internal_id, client_order_id, exchange_order_id),
            )

    def load_id_mappings(self) -> tuple[dict[str, str], dict[str, str]]:
        """Returns (client_to_internal, exchange_to_internal) mappings."""
        client_map: dict[str, str] = {}
        exchange_map: dict[str, str] = {}

        with self._connect() as con:
            cur = con.execute("SELECT internal_id, client_order_id, exchange_order_id FROM id_mappings")
            for row in cur.fetchall():
                internal_id = row[0]
                if row[1]:
                    client_map[row[1]] = internal_id
                if row[2]:
                    exchange_map[row[2]] = internal_id

        return client_map, exchange_map

    # -- Bug C: StopManager active stops persistence --

    def save_stops(self, stops: list[Any]) -> None:
        """Persist active stops for crash recovery (Bug C, Pre-mortem 场景 4).

        Args:
            stops: list of StopHandle dataclass instances. Serialized via
                dataclasses.asdict → JSON. StopSide/StopStatus are str-Enum
                so they round-trip via StopSide(value).
        """
        from dataclasses import asdict
        ts = int(time.time() * 1000)
        with self._connect() as con:
            for handle in stops:
                data = json.dumps(asdict(handle))
                con.execute(
                    "INSERT OR REPLACE INTO live_stops (stop_id, data, updated_at) VALUES (?, ?, ?)",
                    (handle.stop_id, data, ts),
                )

    def load_stops(self) -> list[Any]:
        """Load active stops on restart (Bug C, Pre-mortem 场景 4).

        Returns list of StopHandle instances. StopSide/StopStatus fields
        are reconstructed via StopSide(value) / StopStatus(value).
        """
        from selene_space_exchange.execution.stop_manager import (
            StopHandle,
            StopSide,
            StopStatus,
        )
        with self._connect() as con:
            cur = con.execute("SELECT data FROM live_stops")
            rows = cur.fetchall()
        handles: list[StopHandle] = []
        for (data,) in rows:
            d = json.loads(data)
            # StopSide/StopStatus are str-Enum; reconstruct via ctor
            d["side"] = StopSide(d["side"])
            d["status"] = StopStatus(d["status"])
            handles.append(StopHandle(**d))
        return handles

    # -- Equity Peak (Phase 2b: EquityTracker persistence) --

    def save_equity_peak(
        self, account_id: str, peak: float, peak_date: str
    ) -> None:
        """Persist peak equity + peak_date for an account.

        Args:
            account_id: Account identifier.
            peak: Peak equity value.
            peak_date: ISO-format UTC date string (YYYY-MM-DD).
        """
        now_ms = int(time.time() * 1000)
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO equity_peak
                    (account_id, peak, peak_date, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (account_id, peak, peak_date, now_ms),
            )

    def load_equity_peak(
        self, account_id: str
    ) -> tuple[float, str] | None:
        """Load peak equity + peak_date for an account.

        Args:
            account_id: Account identifier.

        Returns:
            (peak, peak_date_iso) tuple, or None if no record exists.
        """
        with self._connect() as con:
            cur = con.execute(
                "SELECT peak, peak_date FROM equity_peak WHERE account_id = ?",
                (account_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return (float(row[0]), str(row[1]))

    # -- Equity snapshots (Stage B criterion 5: shadow vs paper deviation) --

    def save_equity_snapshot(
        self,
        shadow_equity: float,
        paper_mirror_equity: float,
        deviation_pct: float | None,
    ) -> None:
        """Persist one shadow-vs-paper equity comparison point.

        Called by ShadowExecutionEngine after each fill. verdict.py reads
        these back to verify the <5% deviation acceptance criterion holds
        across the whole run (not just the final snapshot).
        """
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO equity_snapshots
                    (shadow_equity, paper_mirror_equity, deviation_pct, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (shadow_equity, paper_mirror_equity, deviation_pct, int(time.time() * 1000)),
            )

    def load_equity_snapshots(self) -> list[dict[str, float | int | None]]:
        """Load all shadow-vs-paper equity comparison points, oldest first."""
        with self._connect() as con:
            cur = con.execute(
                """
                SELECT shadow_equity, paper_mirror_equity, deviation_pct, timestamp
                FROM equity_snapshots ORDER BY timestamp ASC
                """
            )
            rows = cur.fetchall()
        return [
            {
                "shadow_equity": float(r[0]),
                "paper_mirror_equity": float(r[1]),
                "deviation_pct": float(r[2]) if r[2] is not None else None,
                "timestamp": int(r[3]),
            }
            for r in rows
        ]

    # -- Cleanup --

    def cleanup_old_entries(self, max_age_days: int = 30) -> int:
        cutoff_ms = int((time.time() - max_age_days * 86400) * 1000)
        deleted = 0
        with self._connect() as con:
            cur = con.execute("DELETE FROM live_orders WHERE updated_at < ?", (cutoff_ms,))
            deleted += cur.rowcount
            cur = con.execute("DELETE FROM live_fills WHERE timestamp < ?", (cutoff_ms,))
            deleted += cur.rowcount
        if deleted > 0:
            logger.info("Live state cleanup: removed %d old entries", deleted)
        return deleted

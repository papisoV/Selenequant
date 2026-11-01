"""FillsLedger — persistent record of order fills for realized PnL tracking.

Phase 2b: GREEN implementation with SQLite persistence + realized_pnl computation.

See docs/risk-fills-ledger-design.md for full design.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from selene_space_types.trading import Fill


def _to_db_float(v: Any) -> float:
    """Coerce numeric (Decimal/int/float/str) to float for SQLite binding."""
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _to_decimal(v: Any) -> Decimal:
    """Coerce DB REAL/int/str to Decimal for Fill field round-trip."""
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


class FillsLedger:
    """Persistent ledger of fills for realized PnL aggregation."""

    def __init__(self, db_path: Any | None = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".selenespace" / "risk" / "fills.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._db_path))
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS fills (
                    fill_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    qty REAL NOT NULL,
                    price REAL NOT NULL,
                    fee REAL NOT NULL DEFAULT 0.0,
                    realized_pnl REAL NOT NULL,
                    timestamp INTEGER NOT NULL,
                    account_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_fills_account_ts
                ON fills (account_id, timestamp)
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_fills_order
                ON fills (order_id)
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_fills_symbol_ts
                ON fills (symbol, timestamp)
            """)

    def record_fill(self, fill: Fill, account_id: str = "") -> None:
        """Idempotent insert of a fill. If fill_id exists, no-op.

        Args:
            fill: Fill object from exchange WS/REST event.
            account_id: Account ID for isolation (paper/live/shadow).
        """
        now_ms = int(time.time() * 1000)
        with self._connect() as con:
            con.execute(
                """
                INSERT OR IGNORE INTO fills
                    (fill_id, order_id, symbol, qty, price, fee,
                     realized_pnl, timestamp, account_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.id,
                    fill.order_id,
                    fill.symbol,
                    _to_db_float(fill.quantity),
                    _to_db_float(fill.price),
                    _to_db_float(fill.fee),
                    _to_db_float(fill.realized_pnl),
                    fill.timestamp,
                    account_id,
                    now_ms,
                ),
            )

    def get_daily_realized_pnl(self, account_id: str, date_utc: date) -> float:
        """Sum realized_pnl for account on given UTC date.

        Args:
            account_id: Account ID.
            date_utc: UTC date to query.

        Returns:
            Total realized PnL for the day (negative = loss). 0.0 if no fills.
        """
        start_ms = int(datetime(
            date_utc.year, date_utc.month, date_utc.day, 0, 0, 0, tzinfo=timezone.utc
        ).timestamp() * 1000)
        end_ms = start_ms + 86_400_000  # +1 day

        with self._connect() as con:
            cur = con.execute(
                """
                SELECT COALESCE(SUM(realized_pnl), 0.0)
                FROM fills
                WHERE account_id = ? AND timestamp >= ? AND timestamp < ?
                """,
                (account_id, start_ms, end_ms),
            )
            row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    def get_fills_for_order(self, order_id: str) -> list[Fill]:
        """All fills belonging to a single order (partial fill tracking).

        Args:
            order_id: Client order ID.

        Returns:
            List of Fill objects, ordered by timestamp ASC.
        """
        with self._connect() as con:
            cur = con.execute(
                """
                SELECT fill_id, order_id, symbol, qty, price, fee,
                       realized_pnl, timestamp, account_id
                FROM fills
                WHERE order_id = ?
                ORDER BY timestamp ASC
                """,
                (order_id,),
            )
            rows = cur.fetchall()
        return [self._row_to_fill(r) for r in rows]

    def get_all_fills(
        self, account_id: str, start_ts: int, end_ts: int
    ) -> list[Fill]:
        """Range query for reconciliation / reporting.

        Args:
            account_id: Account ID.
            start_ts: Start timestamp (ms epoch, inclusive).
            end_ts: End timestamp (ms epoch, exclusive).

        Returns:
            List of Fill objects in [start_ts, end_ts).
        """
        with self._connect() as con:
            cur = con.execute(
                """
                SELECT fill_id, order_id, symbol, qty, price, fee,
                       realized_pnl, timestamp, account_id
                FROM fills
                WHERE account_id = ? AND timestamp >= ? AND timestamp < ?
                ORDER BY timestamp ASC
                """,
                (account_id, start_ts, end_ts),
            )
            rows = cur.fetchall()
        return [self._row_to_fill(r) for r in rows]

    def compute_realized_pnl_for_fill(
        self,
        fill: Fill,
        position_avg_entry: float | Decimal | None,
        position_qty_before: float | Decimal,
    ) -> Decimal:
        """Pure function: compute realized_pnl given position state before fill.

        - Buy (open long): returns 0
        - Sell (close long): returns (price - avg_entry) * qty - fee
        - Sell (open short): returns 0
        - Buy (close short): returns (avg_entry - price) * qty - fee
        - Reverse: long→short or short→long, the closing portion realizes PnL

        H19: all math in Decimal so ``0.1 + 0.2 == 0.3`` holds. Inputs may be
        float/Decimal/int/str from exchange API or DB; coerced at entry.

        Args:
            fill: Fill object with side + qty + price + fee.
            position_avg_entry: Average entry price of position before fill (None if flat).
            position_qty_before: Signed position quantity before fill (+ long, - short, 0 flat).

        Returns:
            Realized PnL for this fill (negative = loss).
        """
        qty = _to_decimal(fill.quantity)
        fee = _to_decimal(fill.fee)
        price = _to_decimal(fill.price)
        avg_entry = _to_decimal(position_avg_entry) if position_avg_entry is not None else None
        qty_before = _to_decimal(position_qty_before)

        # Flat position → opening a new position, no realized PnL
        if qty_before == 0 or avg_entry is None:
            return Decimal("0")

        is_long = qty_before > 0
        # Closing portion = min(qty, abs(qty_before))
        closing_qty = qty if qty < abs(qty_before) else abs(qty_before)

        if is_long and fill.side == "sell":
            # Close long: (price - avg_entry) * closing_qty - fee
            return (price - avg_entry) * closing_qty - fee
        elif (not is_long) and fill.side == "buy":
            # Close short: (avg_entry - price) * closing_qty - fee
            return (avg_entry - price) * closing_qty - fee
        else:
            # Adding to existing position (no realized PnL on add)
            # or reverse: only the closing portion realizes PnL,
            # the remaining opening portion is handled by subsequent fills.
            return Decimal("0")

    @staticmethod
    def _row_to_fill(row: tuple) -> Fill:
        """Convert a DB row to a Fill object."""
        return Fill(
            id=row[0],
            order_id=row[1],
            symbol=row[2],
            quantity=_to_decimal(row[3]),
            price=_to_decimal(row[4]),
            fee=_to_decimal(row[5]),
            realized_pnl=_to_decimal(row[6]),
            timestamp=row[7],
            client_order_id=row[1],  # use order_id as client_order_id
            side="",  # not persisted in fills table; caller has fill context
        )

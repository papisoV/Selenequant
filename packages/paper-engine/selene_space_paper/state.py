"""SQLite persistence for the paper-trading world.

All five tables are contractual (downgrades of the DB file must still open).
Row bodies travel as JSON; Decimals go out as strings and come back through
the models' before-validators, so a save/load cycle reproduces digits exactly.
"""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

from selene_space_types.trading import Fill

# Each entry: (table, key column or None for autoincrement id, extra columns).
# Layout is contractual — older DB files must still open against this schema.
_TABLE_SPECS = [
    ("accounts", "account_id", ("data JSON NOT NULL",)),
    ("orders", "key", ("data JSON NOT NULL",)),
    ("positions", "key", ("data JSON NOT NULL",)),
    ("fills", None, ("data JSON NOT NULL",)),
    ("meta", "key", ("value JSON NOT NULL",)),
]


def _build_schema() -> str:
    stmts = []
    for table, pk, cols in _TABLE_SPECS:
        if pk is None:
            body = "id INTEGER PRIMARY KEY AUTOINCREMENT"
        else:
            body = f"{pk} TEXT PRIMARY KEY"
        col_sql = ", ".join([body, *cols])
        stmts.append(f"CREATE TABLE IF NOT EXISTS {table} ({col_sql})")
    return ";\n".join(stmts)


_SCHEMA = _build_schema()

_TABLES = tuple(spec[0] for spec in _TABLE_SPECS)


def _dumps(obj: Any) -> str:
    def _stringify(item: Any) -> Any:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, dict):
            return {k: _stringify(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [_stringify(v) for v in item]
        return item

    return json.dumps(_stringify(obj))


class PaperState:
    """Snapshot / restore / wipe of accounts, orders, positions, fills."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self._dir = Path(state_dir) if state_dir else Path.home() / ".selenespace" / "paper"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db_file = self._dir / "paper.db"
        conn = self._open()
        try:
            self._ensure_tables(conn)
        finally:
            conn.close()

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        # executescript commits implicitly; explicit commit keeps parity with
        # the WAL pragma set in _open.
        conn.executescript(_SCHEMA)
        conn.commit()

    @property
    def state_dir(self) -> Path:
        return self._dir

    @property
    def _db_path(self) -> Path:
        return self._db_file

    def _open(self) -> sqlite3.Connection:
        # WAL lets the API server read snapshots while a save is mid-flight.
        conn = sqlite3.connect(f"{self._db_file}")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _connect(self) -> sqlite3.Connection:
        return self._open()

    def _init_db(self) -> None:
        conn = self._open()
        try:
            self._ensure_tables(conn)
        finally:
            conn.close()

    # ── save ─────────────────────────────────────────────────────────────

    def save_state(
        self,
        account_mgr: Any,
        order_mgr: Any,
        position_mgr: Any,
        *,
        fills: list[Fill] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        conn = self._open()
        try:
            cur = conn.cursor()
            self._write_accounts(cur, account_mgr)
            self._write_orders(cur, order_mgr)
            self._write_positions(cur, position_mgr)
            fill_payloads = [(_dumps(f),) for f in (fills or [])]
            self._replace_rows(cur, "fills", ["data"], "id", fill_payloads)
            meta_rows = [(k, _dumps(v)) for k, v in (extra or {}).items()]
            self._replace_rows(cur, "meta", ["key", "value"], None, meta_rows)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _payload(obj: Any) -> str:
        """Model or dict → JSON text with Decimals preserved as strings."""
        return _dumps(obj)

    def _write_accounts(self, cur: sqlite3.Cursor, account_mgr: Any) -> None:
        rows = [
            (acct_id, self._payload(account))
            for acct_id, account in account_mgr.get_all_data().items()
        ]
        self._replace_rows(cur, "accounts", ["account_id", "data"], "account_id", rows)

    def _write_orders(self, cur: sqlite3.Cursor, order_mgr: Any) -> None:
        bundle = order_mgr.get_all_data()
        book = bundle.get("orders", {}) if isinstance(bundle, dict) else {}
        owners = bundle.get("owner", {}) if isinstance(bundle, dict) else {}
        rows = [
            (
                oid,
                _dumps({"order": order, "account_id": owners.get(oid, "")}),
            )
            for oid, order in book.items()
        ]
        self._replace_rows(cur, "orders", ["key", "data"], "key", rows)

    def _write_positions(self, cur: sqlite3.Cursor, position_mgr: Any) -> None:
        rows: list[tuple[str, str]] = []
        for acct_id, book in position_mgr.get_all_data().items():
            for sym, pos in book.items():
                wrapped = {"position": pos, "account_id": acct_id}
                rows.append((f"{acct_id}::{sym}", _dumps(wrapped)))
        self._replace_rows(cur, "positions", ["key", "data"], "key", rows)

    @staticmethod
    def _replace_rows(
        cur: sqlite3.Cursor,
        table: str,
        columns: list[str],
        conflict_key: str | None,
        rows: list[tuple],
    ) -> None:
        """Swap table contents wholesale, or upsert when ``conflict_key`` is set."""
        cur.execute(f"DELETE FROM {table}")
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        if conflict_key is not None:
            updates = ", ".join(f"{c}=excluded.{c}" for c in columns if c != conflict_key)
            if updates:
                sql += f" ON CONFLICT({conflict_key}) DO UPDATE SET {updates}"
        cur.executemany(sql, rows)

    # ── load ─────────────────────────────────────────────────────────────

    def load_state(
        self, account_mgr: Any, order_mgr: Any, position_mgr: Any
    ) -> tuple[list[Fill], dict[str, Any]]:
        conn = self._open()
        try:
            cur = conn.cursor()
            accounts_in = self._read_accounts(cur)
            orders_in, owners = self._read_orders(cur)
            positions_in = self._read_positions(cur)
            fills_raw = self._read_fills(cur)
            meta_out = self._read_meta(cur)
        finally:
            conn.close()

        account_mgr.load_data(accounts_in)
        order_mgr.load_data({"orders": orders_in, "owner": owners})
        position_mgr.load_data(positions_in)

        # Model validation re-coerces stringified amounts back to Decimal.
        restored_fills = [Fill.model_validate(row) for row in fills_raw]
        return restored_fills, meta_out

    def _read_accounts(self, cur: sqlite3.Cursor) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for acct_id, blob in self._safe_rows(
            cur, "SELECT account_id, data FROM accounts"
        ):
            out[acct_id] = json.loads(blob)
        return out

    @staticmethod
    def _read_orders(cur: sqlite3.Cursor) -> tuple[dict[str, Any], dict[str, str]]:
        book: dict[str, Any] = {}
        owners: dict[str, str] = {}
        for key, blob in PaperState._safe_rows(cur, "SELECT key, data FROM orders"):
            wrapped = json.loads(blob)
            owners[key] = wrapped.get("account_id", "")
            book[key] = wrapped["order"]
        return book, owners

    @staticmethod
    def _read_positions(cur: sqlite3.Cursor) -> dict[str, dict[str, Any]]:
        by_account: dict[str, dict[str, Any]] = {}
        for key, blob in PaperState._safe_rows(cur, "SELECT key, data FROM positions"):
            wrapped = json.loads(blob)
            acct_id = wrapped.get("account_id", "")
            sym = key.split("::", 1)[1] if "::" in key else key
            by_account.setdefault(acct_id, {})[sym] = wrapped["position"]
        return by_account

    @staticmethod
    def _read_fills(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
        rows = PaperState._safe_rows(cur, "SELECT data FROM fills ORDER BY id")
        return [json.loads(blob) for (blob,) in rows]

    @staticmethod
    def _read_meta(cur: sqlite3.Cursor) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in PaperState._safe_rows(cur, "SELECT key, value FROM meta"):
            out[key] = (
                json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value
            )
        return out

    @staticmethod
    def _safe_rows(cur: sqlite3.Cursor, query: str) -> list[tuple]:
        try:
            return list(cur.execute(query))
        except sqlite3.OperationalError:
            return []

    # ── wipe ─────────────────────────────────────────────────────────────

    def clear_state(self) -> None:
        wipe_sql = ";".join(f"DELETE FROM {t}" for t in _TABLES) + ";"
        conn = self._open()
        try:
            conn.executescript(wipe_sql)
            conn.commit()
        finally:
            conn.close()

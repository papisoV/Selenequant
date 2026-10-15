"""Plain-dict persistence for paper trading state.

Accounts, orders, and positions are stored in dedicated tables and passed
around as plain dictionaries — this layer deliberately knows nothing about
pydantic models.  An equity-point history per account is kept alongside for
charting.
"""

from __future__ import annotations

from typing import Any

_ACCOUNT_FIELDS = (
    "name",
    "initial_balance",
    "current_balance",
    "status",
)
_ORDER_FIELDS = (
    "account_id",
    "symbol",
    "side",
    "type",
    "status",
    "quantity",
    "price",
    "filled_quantity",
    "avg_fill_price",
)
_POSITION_FIELDS = (
    "account_id",
    "symbol",
    "side",
    "quantity",
    "avg_entry_price",
    "unrealized_pnl",
    "realized_pnl",
)

_DEFAULTS = {
    "paper_accounts": {
        "initial_balance": 10000.0,
        "current_balance": 10000.0,
        "status": "active",
    },
    "paper_orders": {"status": "created", "quantity": 0.0},
    "paper_positions": {},
}


def _pick(data: dict[str, Any], fields: tuple[str, ...], table: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in fields:
        if field in data:
            out[field] = data[field]
        elif field in _DEFAULTS[table]:
            out[field] = _DEFAULTS[table][field]
    return out


class PaperAccountRepository:
    """CRUD facade over the paper_* tables plus equity history."""

    def __init__(self, db: Any) -> None:
        self._db = db

    # -- accounts ---------------------------------------------------------

    async def save_account(
        self, data: dict[str, Any], *, user_id: str | None = None
    ) -> str:
        cols = _pick(data, _ACCOUNT_FIELDS, "paper_accounts")
        placeholders = ", ".join(f":{k}" for k in cols)
        updates = ", ".join(f"{k} = excluded.{k}" for k in cols)
        sql = (
            f"INSERT INTO paper_accounts (id, {', '.join(cols)}) "
            f"VALUES (:id, {placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET id = excluded.id{',' if updates else ''}{' ' if False else ''}{updates}"
        )
        await self._db.execute(sql, {"id": data["id"], **cols})
        if user_id is not None:
            await self._db.execute(
                "UPDATE paper_accounts SET user_id = ? WHERE id = ?",
                (user_id, data["id"]),
            )
        return str(data["id"])

    async def get_account(
        self, account_id: str, *, user_id: str | None = None
    ) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM paper_accounts WHERE id = ?", (account_id,)
        )
        if not rows:
            return None
        return dict(rows[0])

    # -- orders -----------------------------------------------------------

    async def save_order(self, data: dict[str, Any]) -> str:
        cols = _pick(data, _ORDER_FIELDS, "paper_orders")
        placeholders = ", ".join(f":{k}" for k in cols)
        updates = ", ".join(f"{k} = excluded.{k}" for k in cols)
        sql = (
            f"INSERT INTO paper_orders (id, {', '.join(cols)}) "
            f"VALUES (:id, {placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        await self._db.execute(sql, {"id": data["id"], **cols})
        return str(data["id"])

    async def get_orders(
        self, account_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        if status is not None:
            rows = await self._db.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? AND status = ? ORDER BY created_at",
                (account_id, status),
            )
        else:
            rows = await self._db.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? ORDER BY created_at",
                (account_id,),
            )
        return [dict(r) for r in rows]

    # -- positions ----------------------------------------------------------

    async def save_position(self, data: dict[str, Any]) -> str:
        cols = _pick(data, _POSITION_FIELDS, "paper_positions")
        placeholders = ", ".join(f":{k}" for k in cols)
        updates = ", ".join(f"{k} = excluded.{k}" for k in cols)
        sql = (
            f"INSERT INTO paper_positions (id, {', '.join(cols)}) "
            f"VALUES (:id, {placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        await self._db.execute(sql, {"id": data["id"], **cols})
        return str(data["id"])

    async def get_positions(self, account_id: str) -> list[dict[str, Any]]:
        rows = await self._db.execute(
            "SELECT * FROM paper_positions WHERE account_id = ? ORDER BY symbol",
            (account_id,),
        )
        return [dict(r) for r in rows]

    # -- equity history ------------------------------------------------------

    async def save_equity_point(self, account_id: str, equity: float) -> None:
        await self._db.execute(
            "INSERT INTO paper_equity_history (id, account_id, equity) VALUES (?, ?, ?)",
            (f"eqh-{account_id}-{equity}", account_id, float(equity)),
        )

    async def get_equity_history(
        self, account_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = await self._db.execute(
            "SELECT * FROM paper_equity_history WHERE account_id = ? ORDER BY timestamp DESC LIMIT ?",
            (account_id, int(limit)),
        )
        return [dict(r) for r in rows]

    async def has_equity_history(self, account_id: str) -> bool:
        rows = await self._db.execute(
            "SELECT 1 AS x FROM paper_equity_history WHERE account_id = ? LIMIT 1",
            (account_id,),
        )
        return bool(rows)

    async def clear_equity_history(self, account_id: str) -> None:
        await self._db.execute(
            "DELETE FROM paper_equity_history WHERE account_id = ?", (account_id,)
        )

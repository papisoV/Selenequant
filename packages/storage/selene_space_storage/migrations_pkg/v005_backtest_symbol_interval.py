"""Denormalized market coordinates onto backtest rows.

Stores the symbol and bar interval each run covered directly on the row
so listing endpoints can filter without parsing JSON payloads.
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_COLUMN_ADDS: list[tuple[str, str]] = [
    ("symbol", "ALTER TABLE backtests ADD COLUMN symbol TEXT NOT NULL DEFAULT ''"),
    (
        "interval",
        "ALTER TABLE backtests ADD COLUMN interval TEXT NOT NULL DEFAULT '1h'",
    ),
]


async def _apply(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(backtests)")
    known = {row[1] for row in await cursor.fetchall()}
    for column, ddl in _COLUMN_ADDS:
        if column not in known:
            await conn.execute(ddl)


migration = Migration(
    id="v005_backtest_symbol_interval",
    version=5,
    description="Add symbol and interval columns to backtests",
    apply=_apply,
)

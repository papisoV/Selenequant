"""Chart-support columns for backtests: benchmark curve, drawdown, trades.

Adds three JSON-array columns so a stored backtest can be re-plotted
without recomputation.  Replay-safe against already-extended tables.
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_COLUMN_ADDS: tuple[tuple[str, str], ...] = (
    (
        "buy_hold_curve_json",
        "ALTER TABLE backtests ADD COLUMN buy_hold_curve_json TEXT NOT NULL DEFAULT '[]'",
    ),
    (
        "drawdown_curve_json",
        "ALTER TABLE backtests ADD COLUMN drawdown_curve_json TEXT NOT NULL DEFAULT '[]'",
    ),
    (
        "trades_json",
        "ALTER TABLE backtests ADD COLUMN trades_json TEXT NOT NULL DEFAULT '[]'",
    ),
)

#: 迁移身份。``id`` 一经落库便按它记账, 改了就等同于新增一次迁移。
_MIGRATION_ID = "v004_backtest_curves"
_MIGRATION_VERSION = 4


async def _known_columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cursor.fetchall()}


async def _apply(conn: aiosqlite.Connection) -> None:
    known = await _known_columns(conn, "backtests")
    for column, ddl in _COLUMN_ADDS:
        if column not in known:
            await conn.execute(ddl)


migration = Migration(
    id=_MIGRATION_ID,
    version=_MIGRATION_VERSION,
    description="Add buy_hold_curve, drawdown_curve, and trades columns to backtests",
    apply=_apply,
)

"""Robustness payload column for backtests.

One nullable JSON column so a stored backtest re-surfaces its resampling
robustness block (permutation p-value, bootstrap Sharpe CI, equity percentile
bands) after a restart. Replay-safe against already-extended tables.
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_COLUMN_ADDS: list[tuple[str, str]] = [
    (
        "robustness_json",
        "ALTER TABLE backtests ADD COLUMN robustness_json TEXT NOT NULL DEFAULT '{}'",
    ),
]


async def _apply(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(backtests)")
    known = {row[1] for row in await cursor.fetchall()}
    for column, ddl in _COLUMN_ADDS:
        if column not in known:
            await conn.execute(ddl)


migration = Migration(
    id="v029_backtest_robustness",
    version=29,
    description="Add robustness_json column to backtests",
    apply=_apply,
)

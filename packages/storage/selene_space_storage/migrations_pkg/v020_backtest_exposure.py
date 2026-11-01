"""Exposure curve + honest-assumption columns for backtests.

Three nullable JSON columns so a stored backtest re-surfaces its exposure
series, execution-assumption snapshot, and lookahead warnings after a
restart (previously these only lived in the in-memory result object).
Replay-safe against already-extended tables.
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_COLUMN_ADDS: list[tuple[str, str]] = [
    (
        "exposure_curve_json",
        "ALTER TABLE backtests ADD COLUMN exposure_curve_json TEXT NOT NULL DEFAULT '[]'",
    ),
    (
        "config_snapshot_json",
        "ALTER TABLE backtests ADD COLUMN config_snapshot_json TEXT NOT NULL DEFAULT '{}'",
    ),
    (
        "lookahead_warnings_json",
        "ALTER TABLE backtests ADD COLUMN lookahead_warnings_json TEXT NOT NULL DEFAULT '[]'",
    ),
]


async def _apply(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(backtests)")
    known = {row[1] for row in await cursor.fetchall()}
    for column, ddl in _COLUMN_ADDS:
        if column not in known:
            await conn.execute(ddl)


migration = Migration(
    id="v020_backtest_exposure",
    version=20,
    description="Add exposure_curve, config_snapshot, and lookahead_warnings columns to backtests",
    apply=_apply,
)

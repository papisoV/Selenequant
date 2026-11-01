"""Experiments verification columns — reflection closure (roadmap P0-4).

到期验证六列一次到位:verified_at / was_correct / actual_return_pct /
atr_pct_at_analysis / code_hash / engine_version。前四列支撑 ATR 自适应
判定沉淀,后两列让任何时点的结论可溯源到代码与引擎版本。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_NEW_COLUMNS = (
    "ALTER TABLE experiments ADD COLUMN verified_at TEXT",
    "ALTER TABLE experiments ADD COLUMN was_correct INTEGER",
    "ALTER TABLE experiments ADD COLUMN actual_return_pct REAL",
    "ALTER TABLE experiments ADD COLUMN atr_pct_at_analysis REAL",
    "ALTER TABLE experiments ADD COLUMN code_hash TEXT",
    "ALTER TABLE experiments ADD COLUMN engine_version TEXT",
)

_DUE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_experiments_due "
    "ON experiments (created_at, verified_at)"
)


async def _apply(conn: aiosqlite.Connection) -> None:
    for ddl in _NEW_COLUMNS:
        await conn.execute(ddl)
    await conn.execute(_DUE_INDEX)


migration = Migration(
    id="v012_experiment_verification",
    version=12,
    description="Add verification columns to experiments (reflection closure)",
    apply=_apply,
)

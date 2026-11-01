"""Episodes — strategy experience ledger (S5, RECORDED-only v0).

一 Episode = 一次"策略在真实回测里跑完"的最小经验单位。本表由回测完成
钩子写入,只读 API 消费。metrics 用宽松 JSON blob 是刻意的:BacktestResult
字段集还不稳定,细建模 = 替未来字段设计。provenance v0 只有 RECORDED
(真实跑出),RECONSTRUCTED 等值留给后续版本。
ISO-8601 UTC 字符串存储,字典序即时间序。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_EPISODES_DDL = """
CREATE TABLE IF NOT EXISTS episodes (
    id             TEXT PRIMARY KEY,
    strategy_kind  TEXT NOT NULL,
    strategy_id    TEXT NOT NULL,
    strategy_name  TEXT,
    symbol         TEXT NOT NULL,
    interval       TEXT NOT NULL,
    exchange       TEXT,
    market_type    TEXT,
    params_json    TEXT,
    backtest_id    TEXT,
    metrics_json   TEXT,
    provenance     TEXT NOT NULL DEFAULT 'RECORDED',
    created_at     TEXT NOT NULL
);
"""

_EPISODES_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_episodes_created " "ON episodes (created_at DESC)"
)


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_EPISODES_DDL)
    await conn.execute(_EPISODES_INDEX_DDL)


migration = Migration(
    id="v022_episodes",
    version=22,
    description="Add episodes table (strategy experience ledger, RECORDED-only)",
    apply=_apply,
)

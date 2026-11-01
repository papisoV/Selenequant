"""v026 edge temporal validity — 事实边的有效期区间(刀 1:只加列,零行为变更)。

来源:MiroFish 吸收补扫 round2 判定的**唯一核心缺口**
(N1,见 0919github/scratch/survey-mirofish-bettafish-round2.md)。
MiroFish 的边级四列(created_at/valid_at/invalid_at/expired_at)里,
Selene 只有 created_at —— 即"何时记录",没有"有效到何时"。

后果:一条去年记的"ETF 前有周末效应"和昨天记的,在账本里等权。
recall 的 _SUPERSEDED_HYPOTHESIS_STATUSES 只按 **hypothesis 状态**降权
(refuted/retired),是状态维度;本刀补的是**时间维度**,两者正交。

本迁移**只加列**,不写值、不读值:
- 两列均可空,**NULL = 至今有效**(存量行全部落在这个语义上)
- 无 DEFAULT,无 NOT NULL,无 CHECK —— 语义留给写入侧(刀 2)定义
- 不建索引:research_edges 现仅 27 行(实测 2026-10-05),
  "不给不存在的规模买单"(recall.py:190 既有先例)

刀 2(写入侧填值 + recall(as_of) 过滤)是独立变更,单独规划:
写入侧倾向"因子型事实用数据边界填(boundary_end)、判断型边留空",
**不用 LLM 抽** —— 会把客观时间戳变成模型猜测,
违反事实边不带认识论判断的纪律。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

# 可空、无默认 —— NULL 即"至今有效",存量行天然落在此语义,零行为变更。
_ALTER_EDGES_VALID_AT = "ALTER TABLE research_edges ADD COLUMN valid_at TEXT"
_ALTER_EDGES_INVALID_AT = "ALTER TABLE research_edges ADD COLUMN invalid_at TEXT"


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_ALTER_EDGES_VALID_AT)
    await conn.execute(_ALTER_EDGES_INVALID_AT)


migration = Migration(
    id="v026_edge_temporal_validity",
    version=26,
    description=(
        "Add research_edges.valid_at / invalid_at (both nullable, NULL = still "
        "valid) — temporal validity interval, distinct from created_at"
    ),
    apply=_apply,
)

"""v017 error replay —— 失败经验回放的存储地基。

 Schema semantics derived from rd-agent (https://github.com/microsoft/rd-agent),
 Copyright (c) Microsoft Corporation — MIT License; DDL is Selene-original.

三件套(对应 rd-agent CoSTEER error_query 的扁平化移植,internal-survey 7.2):
1. research_error_types:错误类型字典表,content 精确匹配去重
   (等价 rd-agent error 节点按 content 复用的物理机制)。
2. research_experiments 补 outcome / error_ids_json 两列:
   尝试行自身携带成败与错误关联,根除 rd-agent
   "error_analysis 与 trace 索引错位"的图结构坑。
3. 失败→成功的关联走 research_edges 的新 kind `resolved_by`
   (在 _shared.py 注册,不落在本 migration)。

纯新增:不改既有行,不搬任何 GPL 代码。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_ERROR_TYPES_DDL = """
CREATE TABLE IF NOT EXISTS research_error_types (
    id               TEXT PRIMARY KEY,
    content          TEXT NOT NULL UNIQUE,
    first_seen_at    TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1
);
"""

_EXPERIMENT_OUTCOME_DDL = (
    "ALTER TABLE research_experiments ADD COLUMN outcome TEXT",
    "ALTER TABLE research_experiments ADD COLUMN error_ids_json TEXT "
    "NOT NULL DEFAULT '[]'",
)

_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_research_experiments_outcome "
    "ON research_experiments (hypothesis_id, outcome)",
)


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_ERROR_TYPES_DDL)
    for ddl in _EXPERIMENT_OUTCOME_DDL:
        await conn.execute(ddl)
    for index in _INDEX_DDL:
        await conn.execute(index)


migration = Migration(
    id="v017_error_replay",
    version=17,
    description=(
        "Add research_error_types table + research_experiments "
        "outcome/error_ids_json columns for failure-experience replay"
    ),
    apply=_apply,
)

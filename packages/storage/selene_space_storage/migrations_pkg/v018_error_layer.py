"""v018 error layer —— research_error_types 补 CoSTEER 三层分类列。

CoSTEER 三层语义(rd-agent,MIT License,微软版权声明沿用 v017 文件头):
return_checking = 跑通但结果不合预期;execution = 跑崩;code = 代码质量。
Selene 用规则判层(classify_error),列默认 'undefined',旧行零破坏,
首次以具体层 upsert 同文本时升级。

纯新增列,不改既有行。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_LAYER_DDL = (
    "ALTER TABLE research_error_types ADD COLUMN layer TEXT NOT NULL DEFAULT 'undefined'",
)


async def _apply(conn: aiosqlite.Connection) -> None:
    for ddl in _LAYER_DDL:
        await conn.execute(ddl)


migration = Migration(
    id="v018_error_layer",
    version=18,
    description="Add layer column to research_error_types for CoSTEER three-layer classification",
    apply=_apply,
)

"""Structured sections column for research revisions.

主持人输出本来就是五段固定结构(【时间线】/【观点整合与矛盾】/【深层分析】/
【反向检验】/【新问题】),但在此之前它只是一坨正文 —— "这一版当时有多大
把握""什么条件会推翻它"只能靠人重读。加一列把可判定的字段拎出来。

解析不到就**不写键**(不是写空串),这样"模型没写"与"模型写了空"在库里
可区分;缺段另行落 ``structured_missing`` 事件。Replay-safe:先查后加。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_COLUMN_ADDS: list[tuple[str, str]] = [
    (
        "structured_json",
        "ALTER TABLE research_revisions "
        "ADD COLUMN structured_json TEXT NOT NULL DEFAULT '{}'",
    ),
]


async def _apply(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(research_revisions)")
    known = {row[1] for row in await cursor.fetchall()}
    for column, ddl in _COLUMN_ADDS:
        if column not in known:
            await conn.execute(ddl)


migration = Migration(
    id="v030_revision_structured",
    version=30,
    description="Add structured_json column to research_revisions",
    apply=_apply,
)

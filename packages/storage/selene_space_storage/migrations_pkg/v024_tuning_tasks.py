"""Tuning tasks — LLM 调参任务落库(透明性批2-7)。

`_tuning_tasks` 内存字典在 local-api 重启后全部丢失,任务详情页只剩
"task not found"——用户看不到这个任务跑过什么、跑到哪、结果在哪。
本表 append/update 由 research.py 完成钩子维护,GET /tuning/tasks
合并内存 + 落库两路,重启后历史任务仍可查。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_TUNING_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS tuning_tasks (
    task_id       TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    config_json   TEXT NOT NULL DEFAULT '{}',
    progress_json TEXT NOT NULL DEFAULT '{}',
    result_file   TEXT,
    error         TEXT,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
"""

_TUNING_TASKS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_tuning_tasks_created "
    "ON tuning_tasks (created_at DESC)"
)


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_TUNING_TASKS_DDL)
    await conn.execute(_TUNING_TASKS_INDEX_DDL)


migration = Migration(
    id="v024_tuning_tasks",
    version=24,
    description="Add tuning_tasks table (persist LLM tuning task history across restarts)",
    apply=_apply,
)

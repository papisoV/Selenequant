"""Backtest task table — persistent task state machine (P2-3).

回测任务从"模块级内存 dict"升级为 SQLite 持久真相:状态机
``pending → running → completed|failed`` 原子迁移(条件 UPDATE 防并发双跑),
进程重启后 in-flight 任务标记 failed("server restart") 让用户可见可重提。
created_at/started_at/finished_at 用 epoch 毫秒,与 backtests.py 既有 _tasks 一致。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS backtest_tasks (
    task_id        TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL DEFAULT 'local',
    strategy_id    TEXT NOT NULL,
    strategy_name  TEXT DEFAULT '',
    symbol         TEXT DEFAULT '',
    interval       TEXT DEFAULT '1h',
    status         TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed')),
    error          TEXT,
    created_at     INTEGER NOT NULL,
    started_at     INTEGER,
    finished_at    INTEGER,
    result_id      TEXT
);
"""
_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_backtest_tasks_user_status "
    "ON backtest_tasks (user_id, status)"
)


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_TABLE_DDL)
    await conn.execute(_INDEX_DDL)


migration = Migration(
    id="v014_backtest_tasks",
    version=14,
    description="Add backtest_tasks table for persistent task state machine",
    apply=_apply,
)

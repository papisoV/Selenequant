"""Worker heartbeats table — heartbeat + stale detection (roadmap P1-2).

长驻任务(sentinel 调度器/repair_gaps/paper 跟踪)写一行心跳,
健康端点读 45 秒 stale 判定区分"活着在忙"与"死了"。
ISO-8601 UTC 字符串存储,字典序即时间序,SQLite 无需日期函数。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','stopped','failed')),
    heartbeat_at TEXT NOT NULL,
    metadata_json TEXT
);
"""
_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_role_at "
    "ON worker_heartbeats (role, heartbeat_at DESC)"
)


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_TABLE_DDL)
    await conn.execute(_INDEX_DDL)


migration = Migration(
    id="v013_worker_heartbeats",
    version=13,
    description="Add worker_heartbeats table for liveness tracking",
    apply=_apply,
)

"""Research missions — B1 state machine + B2 activity stream (vertical slice).

Mission = Research Director 的决策对象:同一 unresolved question 的调查工作
单元。research_missions 表承载 B1 生命周期状态机(phase/stage),mission_activity
承载 B2 append-only 活动流(每次状态转移/用户动作/Selene 工作都追加一行,
绝不 UPDATE)。
ISO-8601 UTC 字符串存储,字典序即时间序。
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_MISSIONS_DDL = """
CREATE TABLE IF NOT EXISTS research_missions (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    question       TEXT NOT NULL,
    subject_type    TEXT NOT NULL CHECK (subject_type IN ('strategy','market','event','system')),
    subject_id      TEXT NOT NULL,
    phase           TEXT NOT NULL CHECK (phase IN ('investigation','validation','needs_input','resolved','parked')),
    stage           TEXT NOT NULL DEFAULT 'opened',
    created_by      TEXT NOT NULL DEFAULT 'selene',
    opened_at       TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    resolved_at     TEXT,
    context_json    TEXT NOT NULL DEFAULT '{}',
    outcome_json    TEXT NOT NULL DEFAULT '{}'
);
"""

_MISSIONS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_research_missions_subject "
    "ON research_missions (subject_type, subject_id, phase)"
)
_MISSIONS_UPDATED_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_research_missions_updated "
    "ON research_missions (updated_at DESC)"
)

_ACTIVITY_DDL = """
CREATE TABLE IF NOT EXISTS mission_activity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id  TEXT NOT NULL REFERENCES research_missions (id) ON DELETE CASCADE,
    occurred_at TEXT NOT NULL,
    actor       TEXT NOT NULL CHECK (actor IN ('selene','user','system')),
    kind        TEXT NOT NULL,
    detail_json  TEXT NOT NULL DEFAULT '{}'
);
"""

_ACTIVITY_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_mission_activity_mission_time "
    "ON mission_activity (mission_id, occurred_at, id)"
)


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_MISSIONS_DDL)
    await conn.execute(_MISSIONS_INDEX_DDL)
    await conn.execute(_MISSIONS_UPDATED_DDL)
    await conn.execute(_ACTIVITY_DDL)
    await conn.execute(_ACTIVITY_INDEX_DDL)


migration = Migration(
    id="v015_research_missions",
    version=15,
    description="Add research_missions + mission_activity tables (B1 state machine + B2 activity stream)",
    apply=_apply,
)

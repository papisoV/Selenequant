"""Feed tables backing the recent-events / system-log / reasoning panels.

Three append-heavy stores for the P0c surface:
``events_recent`` (market/session events), ``system_logs`` (structured
log lines with optional exception text), and ``agent_reasoning``
(per-turn agent transcripts with chain metadata).  Each is indexed on
its timestamp so "latest N" queries stay cheap.
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute("""CREATE TABLE IF NOT EXISTS events_recent (
        id TEXT PRIMARY KEY,
        timestamp INTEGER NOT NULL,
        kind TEXT NOT NULL,
        source TEXT,
        symbol TEXT,
        severity TEXT DEFAULT 'info',
        title TEXT,
        body TEXT,
        payload_json TEXT
    )""")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_recent_ts ON events_recent (timestamp DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_recent_kind ON events_recent (kind)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_recent_symbol ON events_recent (symbol)"
    )
    await conn.execute("""CREATE TABLE IF NOT EXISTS system_logs (
        id TEXT PRIMARY KEY,
        timestamp INTEGER NOT NULL,
        level TEXT NOT NULL DEFAULT 'info',
        logger TEXT,
        message TEXT NOT NULL,
        module TEXT,
        line INTEGER,
        exc TEXT
    )""")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_system_logs_ts ON system_logs (timestamp DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_system_logs_level ON system_logs (level)"
    )
    await conn.execute("""CREATE TABLE IF NOT EXISTS agent_reasoning (
        id TEXT PRIMARY KEY,
        timestamp INTEGER NOT NULL,
        session_id TEXT,
        user_id TEXT,
        symbol TEXT,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        chains_json TEXT,
        meta_json TEXT
    )""")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_reasoning_ts ON agent_reasoning (timestamp DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_reasoning_session ON agent_reasoning (session_id)"
    )


migration = Migration(
    id="v008_events_system_logs",
    version=8,
    description="Add events_recent, system_logs, agent_reasoning tables for P0c endpoints",
    apply=_apply,
)

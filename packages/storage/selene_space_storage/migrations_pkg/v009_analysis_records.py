"""Audit store for two-stage market analysis runs.

Each completed two-stage analysis lands here as exactly one row.  The
full replay payload — raw inputs, both prompts and responses, parsed
stage JSONs, token accounting — lives in ``record_json``; the few
fields the listing UI filters on (symbol, interval, entry gate,
decision) are promoted into real columns so they can be indexed.
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration

_RECORDS_DDL = """
    CREATE TABLE IF NOT EXISTS analysis_records (
        id           TEXT PRIMARY KEY,
        timestamp    TEXT NOT NULL,
        input_id     TEXT NOT NULL,
        session_id   TEXT NOT NULL,
        symbol       TEXT,
        interval     TEXT,
        entry_gate   TEXT,
        decision     TEXT,
        record_json  TEXT NOT NULL,
        created_at   TEXT NOT NULL DEFAULT (datetime('now'))
    )
"""
# covering the lookup paths used by list()/list_failures()
_LOOKUP_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_analysis_records_input_id  ON analysis_records (input_id)",
    "CREATE INDEX IF NOT EXISTS idx_analysis_records_created_at ON analysis_records (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_analysis_records_symbol     ON analysis_records (symbol)",
)


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute(_RECORDS_DDL)
    for index_ddl in _LOOKUP_INDEXES:
        await conn.execute(index_ddl)


migration = Migration(
    id="v009_analysis_records",
    version=9,
    description="Add analysis_records for market-analysis two-stage audit persistence",
    apply=_apply,
)

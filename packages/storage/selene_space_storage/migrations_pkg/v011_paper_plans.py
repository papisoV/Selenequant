"""Paper simulation plans — experiment setups logged for manual tracking.

Rather than auto-executing (the paper engine has no entry-trigger or
SL/TP exit loop yet), an experiment's setup is recorded as a plan with a
small lifecycle: planned -> active -> completed, plus planned ->
cancelled as an early exit.  The repository layer enforces legal
transitions and keeps ``updated_at`` fresh.
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS paper_plans (
        id            TEXT PRIMARY KEY,
        experiment_id TEXT,
        name          TEXT NOT NULL,
        symbol        TEXT,
        timeframe     TEXT,
        direction     TEXT,
        entry         REAL,
        stop_loss     REAL,
        take_profit   REAL,
        quantity      REAL,
        account_id    TEXT,
        status        TEXT NOT NULL DEFAULT 'planned',
        note          TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_paper_plans_status      ON paper_plans (status)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_paper_plans_experiment  ON paper_plans (experiment_id)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_paper_plans_created_at  ON paper_plans (created_at DESC)"
    )


migration = Migration(
    id="v011_paper_plans",
    version=11,
    description="Add paper_plans to track experiment setups as paper simulation plans",
    apply=_apply,
)

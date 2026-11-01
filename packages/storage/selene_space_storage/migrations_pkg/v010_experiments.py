"""Registered experiments — the research version-control core object.

Each row freezes one "AI spotted a setup -> setup backtest" cycle:
hypothesis, dataset window, setup parameters (entry/SL/TP/direction),
optional backtest summary, and links to supporting evidence.  The
``parent_experiment`` column records fork lineage so an experiment tree
can be walked from any leaf back to its root.
"""

from __future__ import annotations

import aiosqlite

from selene_space_storage.migrations import Migration


async def _apply(conn: aiosqlite.Connection) -> None:
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS experiments (
        id                 TEXT PRIMARY KEY,
        name               TEXT NOT NULL,
        hypothesis         TEXT,
        symbol             TEXT,
        timeframe          TEXT,
        dataset            TEXT,
        strategy_version   TEXT,
        setup_params       TEXT,
        backtest_result    TEXT,
        evidence_refs      TEXT,
        parent_experiment  TEXT,
        created_at         TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_experiments_created_at ON experiments (created_at DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_experiments_symbol     ON experiments (symbol)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_experiments_parent     ON experiments (parent_experiment)"
    )


migration = Migration(
    id="v010_experiments",
    version=10,
    description="Add experiments for research version-control (Git for Quant Research)",
    apply=_apply,
)

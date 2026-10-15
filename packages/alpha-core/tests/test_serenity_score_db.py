"""Canonical score_db path unification (P2-4).

main.py used a CWD-relative "data/serenity_scores.db" (resolving to
services/local-api/data/ when uvicorn runs there) while serenity_scan.py used
PROJECT_ROOT / "data" / "serenity_scores.db" (repo root). Both writers silently
persisted to different files, so /history never saw CLI-persisted scores. The
default now resolves to one canonical repo-layout path so both share a DB.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from serenity.score_db import SerenityScoreDB, canonical_score_db_path  # noqa: E402

_OLD_SCHEMA_SQL = """
CREATE TABLE serenity_scores (
    symbol TEXT NOT NULL, timestamp INTEGER NOT NULL,
    consensus_scarcity REAL NOT NULL, liquidity_scarcity REAL NOT NULL, trust_scarcity REAL NOT NULL,
    replication_difficulty REAL NOT NULL, certification_progress REAL NOT NULL, prepayment_signal REAL NOT NULL,
    supplier_concentration REAL NOT NULL, evidence_strength REAL NOT NULL,
    penalty_fork_risk REAL NOT NULL, penalty_tvl_concentration REAL NOT NULL, penalty_dev_exodus REAL NOT NULL,
    penalty_revenue_decline REAL NOT NULL, penalty_oi_divergence REAL NOT NULL, penalty_whale_exodus REAL NOT NULL,
    penalty_governance_risk REAL NOT NULL, penalty_liquidity_trap REAL NOT NULL,
    raw_score REAL NOT NULL, normalized_score REAL NOT NULL, tier TEXT NOT NULL,
    funding_rate REAL NOT NULL, oi_change REAL NOT NULL, ls_ratio REAL NOT NULL,
    price_change_7d REAL NOT NULL, volume_change_7d REAL NOT NULL
)
"""

_NEW_SCHEMA_ROW = (
    "ETH/USDT", 2000, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6,
    0.42, 0.8, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08,
    55.0, 0.6, "beta", 0.0001, 0.1, 1.5, 0.02, 0.03,
)


def _make_old_schema_db(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    con.execute(_OLD_SCHEMA_SQL)
    con.execute(
        """INSERT INTO serenity_scores (
            symbol,timestamp,consensus_scarcity,liquidity_scarcity,trust_scarcity,
            replication_difficulty,certification_progress,prepayment_signal,
            supplier_concentration,evidence_strength,penalty_fork_risk,penalty_tvl_concentration,
            penalty_dev_exodus,penalty_revenue_decline,penalty_oi_divergence,penalty_whale_exodus,
            penalty_governance_risk,penalty_liquidity_trap,raw_score,normalized_score,tier,
            funding_rate,oi_change,ls_ratio,price_change_7d,volume_change_7d)
         VALUES ('BTC/USDT',1000,0.1,0.2,0.3,0.4,0.5,0.6,0.3,0.7,0.01,0.02,0.03,0.04,0.05,0.06,0.07,0.08,50.0,0.5,'alpha',0.0001,0.1,1.5,0.02,0.03)"""
    )
    con.commit()
    con.close()


def test_canonical_score_db_path_resolves_to_services_local_api_data():
    path = canonical_score_db_path()
    assert path.is_absolute()
    assert path.parts[-4:] == (
        "services", "local-api", "data", "serenity_scores.db",
    )


def test_default_db_path_is_canonical():
    # Both API (main.py) and CLI (serenity_scan.py) construct without a path.
    assert SerenityScoreDB()._db_path == canonical_score_db_path()


def test_explicit_db_path_still_respected():
    # Tests/other tools can still point elsewhere without going through defaults.
    assert SerenityScoreDB(db_path="/tmp/other.db")._db_path == Path("/tmp/other.db")


def test_d1_migration_renames_old_column_and_preserves_data():
    # D1: a DB created before the marco rename carries supplier_concentration
    # (NOT NULL, no default). Opening it must RENAME the column so INSERTs work
    # and old rows keep their data.
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "old_schema.db")
        _make_old_schema_db(db)

        sdb = SerenityScoreDB(db_path=db)
        sdb.open()
        cols = [r[1] for r in sdb._conn.execute("PRAGMA table_info(serenity_scores)")]
        assert "decentralization" in cols
        assert "supplier_concentration" not in cols
        value = sdb._conn.execute(
            "SELECT decentralization FROM serenity_scores WHERE symbol=?", ("BTC/USDT",)
        ).fetchone()[0]
        assert value == 0.3
        sdb.put_scores([_NEW_SCHEMA_ROW])
        count = sdb._conn.execute("SELECT COUNT(*) FROM serenity_scores").fetchone()[0]
        assert count == 2
        sdb.close()


def test_d1_migration_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "old_schema.db")
        _make_old_schema_db(db)

        for _ in range(2):
            sdb = SerenityScoreDB(db_path=db)
            sdb.open()
            cols = [r[1] for r in sdb._conn.execute("PRAGMA table_info(serenity_scores)")]
            assert cols.count("decentralization") == 1
            sdb.close()

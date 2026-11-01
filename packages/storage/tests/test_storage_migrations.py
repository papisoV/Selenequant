"""Declarative migration framework locks: version-ordered application,
idempotency, late registration, resume after partial run, ledger bookkeeping,
and rollback when an apply step raises."""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from selene_space_storage.migrations import Migration, MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager


async def _make_notes_table(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT)"
    )


async def _add_author_column(conn: aiosqlite.Connection) -> None:
    await conn.execute("ALTER TABLE notes ADD COLUMN author TEXT")


async def _halfway_then_abort(conn: aiosqlite.Connection) -> None:
    await conn.execute("CREATE TABLE doomed (id INTEGER PRIMARY KEY)")
    raise RuntimeError("boom")


def _migration(version: int, description: str, apply) -> Migration:
    return Migration(
        id=f"mig-{version:03d}", version=version, description=description, apply=apply
    )


BOOTSTRAP = (1, "bootstrap notes", _make_notes_table)
EXTEND_AUTHORS = (2, "add author column", _add_author_column)


@pytest_asyncio.fixture
async def conn():
    c = await aiosqlite.connect(":memory:")
    yield c
    await c.close()


@pytest.mark.asyncio
async def test_pending_migrations_apply_and_report(conn: aiosqlite.Connection):
    runner = MigrationRunner([_migration(*BOOTSTRAP)])
    assert await runner.run_pending(conn) == ["bootstrap notes"]

    probe = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='notes'"
    )
    assert (await probe.fetchone()) is not None


@pytest.mark.asyncio
async def test_immediate_rerun_applies_nothing(conn: aiosqlite.Connection):
    runner = MigrationRunner([_migration(*BOOTSTRAP)])
    first_pass = await runner.run_pending(conn)
    second_pass = await runner.run_pending(conn)
    assert first_pass == ["bootstrap notes"]
    assert second_pass == []


@pytest.mark.asyncio
async def test_versions_win_over_registration_order(conn: aiosqlite.Connection):
    runner = MigrationRunner(
        [
            _migration(*EXTEND_AUTHORS),
            _migration(*BOOTSTRAP),
        ]
    )
    assert await runner.run_pending(conn) == ["bootstrap notes", "add author column"]


@pytest.mark.asyncio
async def test_register_after_construction(conn: aiosqlite.Connection):
    runner = MigrationRunner()
    runner.register(_migration(*BOOTSTRAP))
    assert await runner.run_pending(conn) == ["bootstrap notes"]


@pytest.mark.asyncio
async def test_resume_runs_only_the_missing_step(conn: aiosqlite.Connection):
    partial = MigrationRunner([_migration(*BOOTSTRAP)])
    await partial.run_pending(conn)

    complete = MigrationRunner([_migration(*BOOTSTRAP), _migration(*EXTEND_AUTHORS)])
    assert await complete.run_pending(conn) == ["add author column"]


@pytest.mark.asyncio
async def test_ledger_row_describes_each_application(conn: aiosqlite.Connection):
    runner = MigrationRunner([_migration(*BOOTSTRAP)])
    await runner.run_pending(conn)

    cur = await conn.execute("SELECT id, version, description FROM _migrations")
    (ledger_row,) = await cur.fetchall()
    assert ledger_row[0] == "mig-001"
    assert ledger_row[1] == 1
    assert ledger_row[2] == "bootstrap notes"


@pytest.mark.asyncio
async def test_raising_apply_rolls_back_schema_and_ledger(conn: aiosqlite.Connection):
    runner = MigrationRunner([_migration(1, "will explode", _halfway_then_abort)])

    with pytest.raises(RuntimeError, match="boom"):
        await runner.run_pending(conn)

    ghost = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='doomed'"
    )
    assert (await ghost.fetchone()) is None

    ledger_hit = await conn.execute("SELECT id FROM _migrations WHERE id='mig-001'")
    assert (await ledger_hit.fetchone()) is None


# -- v019 mission_thesis: registry + schema + CHECK enforcement ------------------


@pytest.mark.asyncio
async def test_v019_registered_after_v018():
    # v019 让号后不再是 ALL_MIGRATIONS[-1](main 侧 v018/v020 已入列);
    # 改为对账式断言:v018(v017 之后) → v019(其前) 均在注册表内且顺序正确。
    ids = [m.id for m in ALL_MIGRATIONS]
    assert "v018_error_layer" in ids
    assert "v019_mission_thesis" in ids
    assert ids.index("v018_error_layer") == ids.index("v017_error_replay") + 1
    assert ids.index("v019_mission_thesis") == ids.index("v018_error_layer") + 1


@pytest_asyncio.fixture
async def thesis_db():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield db


async def _seed_mission(db) -> str:
    rows = await db.execute(
        """
        INSERT INTO research_missions
            (id, title, question, subject_type, subject_id, phase, stage,
             created_by, opened_at, updated_at)
        VALUES ('mis-test01', 't', 'q', 'strategy', 'st-x', 'investigation',
                'opened', 'selene', '2026-09-23T00:00:00+00:00',
                '2026-09-23T00:00:00+00:00')
        RETURNING id
        """
    )
    mission_id = rows[0]["id"]
    assert isinstance(mission_id, str)
    return mission_id


@pytest.mark.asyncio
async def test_v019_creates_thesis_tables_with_expected_columns(thesis_db):
    pillar_cols = {
        r["name"] for r in await thesis_db.execute("PRAGMA table_info(mission_pillars)")
    }
    evidence_cols = {
        r["name"]
        for r in await thesis_db.execute("PRAGMA table_info(mission_evidence)")
    }
    assert {
        "id",
        "mission_id",
        "claim",
        "priority",
        "baseline",
        "expected_path",
        "confirm_threshold",
        "warning_threshold",
        "break_threshold",
        "threshold_provenance",
        "latest_evidence_id",
        "signal",
        "evidence_quality",
        "provenance",
        "model_line",
        "implied_action",
        "next_proof_point",
        "next_proof_source",
        "owner",
        "status",
        "status_note",
        "superseded_by",
        "created_at",
        "updated_at",
    } <= pillar_cols
    assert {
        "seq",
        "id",
        "mission_id",
        "pillar_id",
        "occurred_at",
        "source_type",
        "source_ref",
        "fact",
        "prior_expectation",
        "market_expectation",
        "interpretation",
        "signal",
        "evidence_quality",
        "impact_json",
        "follow_up",
        "owner",
        "created_at",
    } <= evidence_cols


@pytest.mark.asyncio
async def test_v019_check_constraints_reject_unknown_vocab(thesis_db):
    mission_id = await _seed_mission(thesis_db)
    await thesis_db.execute(
        "INSERT INTO mission_pillars (id, mission_id, claim, priority, created_at, updated_at) "
        "VALUES ('pil-ok01', ?, 'claim', 'core', '2026-09-23T00:00:00+00:00', "
        "'2026-09-23T00:00:00+00:00')",
        (mission_id,),
    )
    with pytest.raises(Exception, match="CHECK"):
        await thesis_db.execute(
            "INSERT INTO mission_evidence (id, mission_id, pillar_id, occurred_at, "
            "source_type, fact, interpretation, signal, created_at) "
            "VALUES ('ev-badsig', ?, 'pil-ok01', '2026-09-23T00:00:00+00:00', "
            "'backtest', 'fact', 'interp', 'very_good', '2026-09-23T00:00:00+00:00')",
            (mission_id,),
        )
    with pytest.raises(Exception, match="CHECK"):
        await thesis_db.execute(
            "UPDATE mission_pillars SET status = 'vibes' WHERE id = 'pil-ok01'"
        )

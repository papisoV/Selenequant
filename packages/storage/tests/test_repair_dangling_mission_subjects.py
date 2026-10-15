"""Repair script for strategy-typed missions with a non-strategy subject.

Proves the APPLY path on an in-memory DB seeded with the same two rows the
shared DB carried (2026-10-13), so the production write is not the first time
the code runs.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from scripts.repair_dangling_mission_subjects import _TARGETS, repair
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import check_graph_integrity
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


async def _seed(db) -> None:
    """Seed the two known-bad rows verbatim (ids/subjects/context from prod)."""
    repo = ResearchMissionRepository(db)
    # Written through raw SQL because the L1 gate now rejects these exact
    # subjects — which is the point: they predate the gate.
    await db.execute(
        "INSERT INTO research_missions "
        "(id, title, question, subject_type, subject_id, phase, stage, "
        " created_by, opened_at, updated_at, context_json) "
        "VALUES (?, ?, ?, 'strategy', ?, 'investigation', 'opened', ?, ?, ?, ?)",
        (
            "mis-b2f9056975c5",
            "BTC 1h funding 延迟衰减",
            "half_delay 在哪一档?",
            "entry-delay-ic-probe",
            "user",
            "2026-10-03T14:41:42+00:00",
            "2026-10-03T14:43:09+00:00",
            '{"pipeline": "question-promote", "question_id": "q-a5cc113a11ce"}',
        ),
    )
    await db.execute(
        "INSERT INTO research_missions "
        "(id, title, question, subject_type, subject_id, phase, stage, "
        " created_by, opened_at, updated_at, context_json) "
        "VALUES (?, ?, ?, 'strategy', ?, 'validation', 'opened', ?, ?, ?, ?)",
        (
            "mis-3adeb596e736",
            "验收种子 · 配置漂移裁决演示",
            "该策略是否仍能兑现声明收益?",
            "bt-b62d32ac",
            "verification-seed",
            "2026-09-07T09:38:16+00:00",
            "2026-09-12T18:17:22+00:00",
            "{}",
        ),
    )
    # A healthy strategy mission must survive untouched.
    await repo.create(
        title="Keltner V2 声明漂移调查",
        question="SOL 4h 实盘亏损是配置漂移还是框架失效?",
        subject_type="system",
        subject_id="keep-me",
    )


async def _dangling(db) -> set[str]:
    report = await check_graph_integrity(db)
    return {
        v["object_id"]
        for v in report["violations"]
        if v["rule"] == "mission_subject_dangling"
    }


@pytest.mark.asyncio
async def test_dry_run_changes_nothing(db):
    await _seed(db)
    changed, remaining = await repair(db, apply=False)
    assert changed == 2
    assert remaining == 2  # dry run leaves both dangling
    assert await _dangling(db) == {"mis-b2f9056975c5", "mis-3adeb596e736"}


@pytest.mark.asyncio
async def test_apply_clears_dangling_and_preserves_subject_id(db):
    await _seed(db)
    changed, remaining = await repair(db, apply=True)
    assert changed == 2
    assert remaining == 0

    repo = ResearchMissionRepository(db)
    for mission_id, subject_id, _type, extra in _TARGETS:
        m = await repo.get(mission_id)
        assert m is not None
        assert m["subject_type"] == "system"  # reclassified
        assert m["subject_id"] == subject_id  # preserved verbatim
        assert m["context"]["subject_reclassified"]["reason"]
        for k, v in extra.items():
            assert m["context"]["subject_reclassified"][k] == v
        # an audit row was appended
        activity = await repo.list_activity(mission_id)
        assert any(a["kind"] == "subject_reclassified" for a in activity)


@pytest.mark.asyncio
async def test_apply_is_idempotent(db):
    await _seed(db)
    await repair(db, apply=True)
    # Second pass: subjects are already 'system', so nothing to do.
    changed, remaining = await repair(db, apply=True)
    assert changed == 0
    assert remaining == 0


@pytest.mark.asyncio
async def test_aborts_when_subject_drifted(db):
    await _seed(db)
    # Simulate someone having already edited the subject to something else.
    await db.execute(
        "UPDATE research_missions SET subject_id = 'something-else' WHERE id = ?",
        ("mis-b2f9056975c5",),
    )
    with pytest.raises(ValueError, match="拒绝盲改"):
        await repair(db, apply=True)

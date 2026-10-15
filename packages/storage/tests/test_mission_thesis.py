"""Mission thesis repository acceptance tests (plan S2, 7 验收组).

核心契约:append_evidence 三写单事务 + 快照 (occurred_at, seq) tuple 严格
大于账本派生基线才推进 + 账本 append-only(无 UPDATE/DELETE 方法)。
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.mission_thesis import (
    ACT_EVIDENCE_APPENDED,
    ACT_PILLAR_SUPERSEDED,
    ACT_STATUS_SET,
    MissionThesisRepository,
    aggregate_status,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def thesis_env():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        await db.execute(
            """
            INSERT INTO research_missions
                (id, title, question, subject_type, subject_id, phase, stage,
                 created_by, opened_at, updated_at)
            VALUES ('mis-t', 't', 'q', 'strategy', 'st', 'investigation',
                    'opened', 'selene', '2026-09-23T00:00:00+00:00',
                    '2026-09-23T00:00:00+00:00')
            """
        )
        yield MissionThesisRepository(db), db


async def _pillar(repo, claim="keltner breakout persists", priority="core") -> str:
    pillar_id = await repo.add_pillar("mis-t", claim, priority)
    assert isinstance(pillar_id, str)
    return pillar_id


# 1. add_pillar: falsifiable claim discipline ----------------------------------


@pytest.mark.asyncio
async def test_add_pillar_rejects_empty_claim_and_bad_vocab(thesis_env):
    repo, _ = thesis_env
    with pytest.raises(ValueError, match="non-empty"):
        await repo.add_pillar("mis-t", "   ", "core")
    with pytest.raises(ValueError, match="unknown priority"):
        await repo.add_pillar("mis-t", "claim", "critical")
    with pytest.raises(ValueError, match="unknown provenance"):
        await repo.add_pillar("mis-t", "claim", "core", provenance="guess")


@pytest.mark.asyncio
async def test_add_pillar_unknown_mission_raises(thesis_env):
    repo, _ = thesis_env
    with pytest.raises(KeyError, match="mission not found"):
        await repo.add_pillar("mis-nope", "claim", "core")


@pytest.mark.asyncio
async def test_add_pillar_defaults_and_activity(thesis_env):
    repo, db = thesis_env
    pillar_id = await _pillar(repo)
    pillar = await repo.get_pillar(pillar_id)
    assert pillar["status"] == "untested"
    assert pillar["signal"] == "untested"
    assert pillar["threshold_provenance"] == "draft"
    acts = await db.execute(
        "SELECT kind, detail_json FROM mission_activity WHERE mission_id='mis-t' ORDER BY id"
    )
    kinds = [a["kind"] for a in acts]
    assert kinds == ["pillar_opened"]


# 2. append_evidence 三点契约 ----------------------------------------------------


@pytest.mark.asyncio
async def test_append_evidence_validates_signal_and_mission_membership(thesis_env):
    repo, _ = thesis_env
    pillar_id = await _pillar(repo)
    with pytest.raises(ValueError, match="unknown signal"):
        await repo.append_evidence(
            "mis-t",
            pillar_id,
            occurred_at="2026-09-20",
            source_type="backtest",
            fact="f",
            interpretation="i",
            signal="great",
        )
    with pytest.raises(ValueError, match="does not belong"):
        await repo.append_evidence(
            "mis-other",
            pillar_id,
            occurred_at="2026-09-20",
            source_type="backtest",
            fact="f",
            interpretation="i",
            signal="confirming",
        )


@pytest.mark.asyncio
async def test_append_evidence_advances_snapshot_in_single_transaction(thesis_env):
    repo, db = thesis_env
    pillar_id = await _pillar(repo)
    ev_id = await repo.append_evidence(
        "mis-t",
        pillar_id,
        occurred_at="2026-09-22T10:00:00+00:00",
        source_type="backtest",
        source_ref="bt-x",
        fact="241 trades, sharpe 1.9",
        interpretation="edge holds",
        signal="confirming",
        impact={"sharpe": 1.9},
    )
    pillar = await repo.get_pillar(pillar_id)
    assert pillar["latest_evidence_id"] == ev_id
    assert pillar["signal"] == "confirming"
    row = await repo.get_evidence(ev_id)
    assert row["impact"] == {"sharpe": 1.9}
    acts = await db.execute(
        "SELECT kind FROM mission_activity WHERE mission_id='mis-t' ORDER BY id"
    )
    assert [a["kind"] for a in acts] == [
        "pillar_opened",
        ACT_EVIDENCE_APPENDED,
    ]


@pytest.mark.asyncio
async def test_append_evidence_backfill_does_not_override_snapshot(thesis_env):
    repo, _ = thesis_env
    pillar_id = await _pillar(repo)
    await repo.append_evidence(
        "mis-t",
        pillar_id,
        occurred_at="2026-09-22T10:00:00+00:00",
        source_type="backtest",
        fact="latest",
        interpretation="i1",
        signal="confirming",
    )
    backfill_id = await repo.append_evidence(
        "mis-t",
        pillar_id,
        occurred_at="2026-09-20T08:00:00+00:00",
        source_type="backtest",
        fact="older backfilled",
        interpretation="i2",
        signal="strongly_weakening",
    )
    pillar = await repo.get_pillar(pillar_id)
    assert pillar["signal"] == "confirming"
    assert pillar["latest_evidence_id"] != backfill_id
    assert len(await repo.list_evidence("mis-t")) == 2


@pytest.mark.asyncio
async def test_append_evidence_same_timestamp_tiebreak_by_seq(thesis_env):
    repo, _ = thesis_env
    pillar_id = await _pillar(repo)
    await repo.append_evidence(
        "mis-t",
        pillar_id,
        occurred_at="2026-09-22T10:00:00",
        source_type="backtest",
        fact="first",
        interpretation="i1",
        signal="mildly_confirming",
    )
    second_id = await repo.append_evidence(
        "mis-t",
        pillar_id,
        occurred_at="2026-09-22T10:00:00",
        source_type="backtest",
        fact="second same ts",
        interpretation="i2",
        signal="weakening",
    )
    pillar = await repo.get_pillar(pillar_id)
    assert pillar["latest_evidence_id"] == second_id
    assert pillar["signal"] == "weakening"


# 3. ledger append-only ---------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_ledger_has_no_update_or_delete_path(thesis_env):
    repo, db = thesis_env
    pillar_id = await _pillar(repo)
    ev_id = await repo.append_evidence(
        "mis-t",
        pillar_id,
        occurred_at="2026-09-22",
        source_type="backtest",
        fact="f",
        interpretation="i",
        signal="neutral",
    )
    write_methods = [m for m in dir(repo) if m.startswith(("update_", "delete_"))]
    assert not write_methods
    row = await db.execute(
        "SELECT COUNT(*) AS n FROM mission_evidence WHERE id=?", (ev_id,)
    )
    assert row[0]["n"] == 1


# 4. status transition + supersede -----------------------------------------------


@pytest.mark.asyncio
async def test_set_pillar_status_records_history_in_activity(thesis_env):
    repo, db = thesis_env
    pillar_id = await _pillar(repo)
    with pytest.raises(ValueError, match="unknown pillar status"):
        await repo.set_pillar_status(pillar_id, "vibes")
    updated = await repo.set_pillar_status(pillar_id, "intact", status_note="ok")
    assert updated["status"] == "intact"
    with pytest.raises(KeyError, match="pillar not found"):
        await repo.set_pillar_status("pil-nope", "intact")
    acts = await db.execute(
        "SELECT kind, detail_json FROM mission_activity WHERE kind=?",
        (ACT_STATUS_SET,),
    )
    detail = json.loads(acts[0]["detail_json"])
    assert acts[0]["kind"] == ACT_STATUS_SET
    assert detail["from"] == "untested"
    assert detail["to"] == "intact"


@pytest.mark.asyncio
async def test_set_pillar_status_guarded_against_concurrent_change(thesis_env):
    """Guarded update 形态: read-then-write 间的并发跳变必须报冲突.

    竞态窗口在 set_pillar_status() 内部 get_pillar() 与 UPDATE 之间: 让第一
    次读返回旧快照(untested), 并发写者此时直接 SQL 把 status 推到 broken;
    guarded UPDATE (WHERE status=untested) 不命中 → 必须报冲突, 活动流的
    "from" 值绝不写已过期的旧状态。
    """
    repo, db = thesis_env
    pillar_id = await _pillar(repo)
    real_get = repo.get_pillar
    snapshot = await real_get(pillar_id)

    calls = {"n": 0}

    async def stale_then_real(pid: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return snapshot
        return await real_get(pid)

    repo.get_pillar = stale_then_real  # type: ignore[method-assign]
    try:
        await db.execute(
            "UPDATE mission_pillars SET status = 'broken' WHERE id = ?",
            (pillar_id,),
        )
        with pytest.raises(ValueError, match="changed concurrently"):
            await repo.set_pillar_status(pillar_id, "intact")
    finally:
        repo.get_pillar = real_get  # type: ignore[method-assign]

    pillar = await real_get(pillar_id)
    assert pillar["status"] == "broken"


@pytest.mark.asyncio
async def test_supersede_writes_pointer_not_field_rewrite(thesis_env):
    repo, db = thesis_env
    old_id = await _pillar(repo, claim="old claim")
    new_id = await _pillar(repo, claim="refined claim")
    await repo.supersede_pillar(old_id, new_id, reason="threshold provenance approved")
    old = await repo.get_pillar(old_id)
    assert old["claim"] == "old claim"
    assert old["superseded_by"] == new_id
    acts = await db.execute(
        "SELECT detail_json FROM mission_activity WHERE kind=?",
        (ACT_PILLAR_SUPERSEDED,),
    )
    supersede_detail = json.loads(acts[0]["detail_json"])
    assert supersede_detail["old_id"] == old_id
    assert supersede_detail["reason"] == "threshold provenance approved"


# 5. aggregate_status 纯函数 ------------------------------------------------------


def test_aggregate_core_broken_propagates():
    verdict = aggregate_status(
        [
            {"priority": "core", "status": "broken", "superseded_by": None},
            {"priority": "supporting", "status": "intact", "superseded_by": None},
        ]
    )
    assert verdict == {"aggregate": "broken", "requires_override": False}


def test_aggregate_two_impaired_core():
    verdict = aggregate_status(
        [
            {"priority": "core", "status": "impaired", "superseded_by": None},
            {"priority": "core", "status": "impaired", "superseded_by": None},
        ]
    )
    assert verdict == {"aggregate": "impaired", "requires_override": False}


def test_aggregate_single_impaired_requires_override():
    verdict = aggregate_status(
        [
            {"priority": "core", "status": "impaired", "superseded_by": None},
            {"priority": "supporting", "status": "intact", "superseded_by": None},
        ]
    )
    assert verdict == {"aggregate": "watch", "requires_override": True}


def test_aggregate_tie_intact_beats_strengthening():
    verdict = aggregate_status(
        [
            {"priority": "supporting", "status": "intact", "superseded_by": None},
            {
                "priority": "supporting",
                "status": "strengthening",
                "superseded_by": None,
            },
        ]
    )
    assert verdict == {"aggregate": "intact", "requires_override": False}


def test_aggregate_all_untested_and_superseded_excluded():
    verdict = aggregate_status(
        [
            {"priority": "core", "status": "untested", "superseded_by": None},
            {"priority": "supporting", "status": "broken", "superseded_by": "pil-new"},
            {"priority": "supporting", "status": "retired", "superseded_by": None},
        ]
    )
    assert verdict == {"aggregate": "untested", "requires_override": False}


# 6. drift check -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_drift_check_requires_all_five_patterns(thesis_env):
    repo, db = thesis_env
    partial = {
        k: {"present": False}
        for k in (
            "reason_changed",
            "catalyst_laundering",
            "valuation_substitution",
            "dismissal_by_horizon",
        )
    }
    with pytest.raises(ValueError, match="missing patterns"):
        await repo.record_drift_check("mis-t", partial)
    full = {
        **partial,
        "protection_as_reason": {"present": True, "note": "stop honored"},
    }
    await repo.record_drift_check("mis-t", full, checked_by="user", note="weekly")
    acts = await db.execute(
        "SELECT detail_json FROM mission_activity WHERE kind='drift_check'"
    )
    detail = json.loads(acts[0]["detail_json"])
    assert detail["patterns"]["protection_as_reason"] == {
        "present": True,
        "note": "stop honored",
    }
    assert detail["checked_by"] == "user"


# 7. stale proof points ----------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_stale_proof_points_with_injected_now(thesis_env):
    repo, _ = thesis_env
    stale_id = await repo.add_pillar("mis-t", "claim A", "core")
    await repo._db.execute(
        "UPDATE mission_pillars SET next_proof_point='2026-09-10' WHERE id=?",
        (stale_id,),
    )
    fresh_id = await repo.add_pillar("mis-t", "claim B", "supporting")
    await repo._db.execute(
        "UPDATE mission_pillars SET next_proof_point='2026-09-25' WHERE id=?",
        (fresh_id,),
    )
    hits = await repo.detect_stale_proof_points("mis-t", grace_days=7, now="2026-09-23")
    assert [h["pillar_id"] for h in hits] == [stale_id]
    assert hits[0]["overdue_days"] == 13
    acts = await repo._db.execute(
        "SELECT detail_json FROM mission_activity WHERE kind='drift_hint'"
    )
    assert json.loads(acts[0]["detail_json"])["overdue_days"] == 13


@pytest.mark.asyncio
async def test_detect_stale_ignores_pillars_with_post_date_evidence(thesis_env):
    repo, _ = thesis_env
    pillar_id = await _pillar(repo)
    await repo._db.execute(
        "UPDATE mission_pillars SET next_proof_point='2026-09-10' WHERE id=?",
        (pillar_id,),
    )
    await repo.append_evidence(
        "mis-t",
        pillar_id,
        occurred_at="2026-09-21",
        source_type="backtest",
        fact="fresh proof",
        interpretation="i",
        signal="confirming",
    )
    hits = await repo.detect_stale_proof_points("mis-t", grace_days=7, now="2026-09-23")
    assert hits == []

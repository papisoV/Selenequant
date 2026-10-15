"""collect_changes 聚合测试 —— 主窗口第一刀 storage 半边。

用裸 INSERT 造种子(同 test_research_graph_recall.py 手法),独立于对象仓储。
核心断言:tier1 常驻不受 since 过滤;tier2/3 受 since;limit 排序后裁剪;
consequence 只含 DB 当前值不含因果词。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import collect_changes
from selene_space_storage.sqlite import AsyncSQLiteManager

T0 = "2026-09-01T00:00:00+00:00"
NOW = "2026-09-10T00:00:00+00:00"


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


async def _seed_question(
    conn,
    qid: str,
    *,
    title: str = "测试问题",
    created_by: str = "director",
    parent_question_id: str | None = None,
    status: str = "open",
    opened_at: str = T0,
) -> None:
    await conn.execute(
        "INSERT INTO research_questions (id, title, body, anchor_json, status, "
        "parent_question_id, opened_at, updated_at, created_by) "
        "VALUES (?, ?, '', '{}', ?, ?, ?, ?, ?)",
        (qid, title, status, parent_question_id, opened_at, opened_at, created_by),
    )


async def _seed_event(
    conn,
    eid: str,
    qid: str,
    kind: str,
    *,
    occurred_at: str = T0,
    actor: str = "user",
    detail: str = "{}",
) -> None:
    await conn.execute(
        "INSERT INTO research_question_events (question_id, occurred_at, actor, "
        "kind, detail_json) VALUES (?, ?, ?, ?, ?)",
        (qid, occurred_at, actor, kind, detail),
    )
    assert eid  # 事件 id 是自增的,eid 只用于测试可读性


async def _seed_decision(
    conn,
    did: str,
    qid: str,
    *,
    outcome_status: str = "outcome_pending",
    window_end: str | None = None,
    decided_at: str = T0,
) -> None:
    await conn.execute(
        "INSERT INTO research_decisions (id, question_id, verdict, decided_by, "
        "outcome_status, window_end, decided_at) "
        "VALUES (?, ?, 'hold', 'director', ?, ?, ?)",
        (did, qid, outcome_status, window_end, decided_at),
    )


async def _seed_revision(
    conn,
    rid: str,
    qid: str,
    seq: int,
    *,
    cited: str = "[]",
    created_at: str = T0,
) -> None:
    await conn.execute(
        "INSERT INTO research_revisions (id, question_id, seq, content, "
        "cited_json, actor, created_at) VALUES (?, ?, ?, 'v', ?, 'selene', ?)",
        (rid, qid, seq, cited, created_at),
    )


async def _seed_evidence_with_address(
    conn,
    evid: str,
    qid: str,
    *,
    mode: str = "RECORDED",
    created_at: str = T0,
) -> None:
    await conn.execute(
        "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
        "boundary_start, boundary_end, occurred_at, created_at) "
        "VALUES ('ep-x', 'market', 'BTC/USDT', 'ep', ?, ?, ?, ?)",
        (created_at, created_at, created_at, created_at),
    )
    await conn.execute(
        "INSERT INTO research_evidence (id, episode_id, mode, payload_json, "
        "observed_at, created_at) VALUES (?, 'ep-x', ?, '{}', ?, ?)",
        (evid, mode, created_at, created_at),
    )
    await conn.execute(
        "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
        "kind, edge_class, actor, created_at) "
        "VALUES (?, 'evidence', ?, 'question', ?, 'addresses', 'fact', "
        "'selene', ?)",
        (f"e-{evid}", evid, qid, created_at),
    )


# ---- tier1:三信号 + 常驻 -------------------------------------------------


@pytest.mark.asyncio
async def test_tier1_proposal_present(db):
    async with db.connection() as conn:
        await _seed_question(conn, "q-parent", title="父问题")
        await _seed_question(
            conn,
            "q-prop",
            title="提议问题",
            created_by="selene",
            parent_question_id="q-parent",
        )
    result = await collect_changes(db, now=NOW)
    t1 = [c for c in result["changes"] if c["tier"] == 1]
    assert len(t1) == 1
    assert t1[0]["source"] == "proposal"
    assert t1[0]["needs_director"] is True
    assert "待拍板" in t1[0]["consequence"]
    assert "父问题" in t1[0]["consequence"]


@pytest.mark.asyncio
async def test_tier1_window_expired_decision(db):
    async with db.connection() as conn:
        await _seed_question(conn, "q-1")
        await _seed_decision(
            conn, "dec-1", "q-1", window_end="2026-09-05T00:00:00+00:00"
        )
    result = await collect_changes(db, now=NOW)
    t1 = [c for c in result["changes"] if c["tier"] == 1]
    assert len(t1) == 1
    assert t1[0]["kind"] == "window_expired"
    assert "到期" in t1[0]["consequence"]
    assert "pending" in t1[0]["consequence"]


@pytest.mark.asyncio
async def test_tier1_decision_pending_no_window(db):
    async with db.connection() as conn:
        await _seed_question(conn, "q-1")
        await _seed_decision(conn, "dec-1", "q-1", window_end=None)
    result = await collect_changes(db, now=NOW)
    t1 = [c for c in result["changes"] if c["tier"] == 1]
    assert len(t1) == 1
    assert t1[0]["kind"] == "decision_pending"
    assert "待记录" in t1[0]["consequence"]


@pytest.mark.asyncio
async def test_tier1_open_window_not_reported(db):
    async with db.connection() as conn:
        await _seed_question(conn, "q-1")
        await _seed_decision(
            conn, "dec-1", "q-1", window_end="2026-09-20T00:00:00+00:00"
        )
    result = await collect_changes(db, now=NOW)
    assert result["changes"] == []
    assert result["counts"] == {"t1": 0, "t2": 0, "t3": 0}


@pytest.mark.asyncio
async def test_tier1_survives_since_filter(db):
    """tier1 是持续状态:提议早于 since 仍出现;tier2/3 被滤掉。"""
    async with db.connection() as conn:
        await _seed_question(conn, "q-parent")
        await _seed_question(
            conn, "q-prop", created_by="selene", parent_question_id="q-parent"
        )
        await _seed_event(conn, "e-1", "q-prop", "closed", occurred_at=T0)
    since = "2026-09-05T00:00:00+00:00"
    result = await collect_changes(db, since=since, now=NOW)
    tiers = [(c["tier"], c["source"]) for c in result["changes"]]
    assert (1, "proposal") in tiers
    assert all(t != 2 for t, _ in tiers)


# ---- tier2/tier3:来源与过滤 -----------------------------------------------


@pytest.mark.asyncio
async def test_tier2_status_event_and_revision(db):
    async with db.connection() as conn:
        await _seed_question(conn, "q-1", title="甲")
        await _seed_event(
            conn, "e-1", "q-1", "closed", detail='{"from": "open", "to": "parked"}'
        )
        await _seed_revision(conn, "rev-1", "q-1", 1)
    result = await collect_changes(db, now=NOW)
    t2 = [c for c in result["changes"] if c["tier"] == 2]
    assert {c["source"] for c in t2} == {"event", "revision"}
    closed = next(c for c in t2 if c["source"] == "event")
    assert "parked" in closed["consequence"]
    rev = next(c for c in t2 if c["source"] == "revision")
    assert "v1" in rev["consequence"] and "1 版" in rev["consequence"]


@pytest.mark.asyncio
async def test_tier2_revision_filtered_by_since(db):
    async with db.connection() as conn:
        await _seed_question(conn, "q-1")
        await _seed_revision(conn, "rev-1", "q-1", 1, created_at=T0)
    result = await collect_changes(db, since="2026-09-05T00:00:00+00:00", now=NOW)
    assert all(c["tier"] != 2 for c in result["changes"])


@pytest.mark.asyncio
async def test_tier3_events_and_evidence_needs_edge(db):
    async with db.connection() as conn:
        await _seed_question(conn, "q-1")
        await _seed_event(conn, "e-1", "q-1", "investigate_perspective", actor="selene")
        # 有 addresses 边 → 进流
        await _seed_evidence_with_address(conn, "ev-1", "q-1")
        # 无边 evidence → 不进流(先插独立 episode 防外键撞 ep-x)
        await conn.execute(
            "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
            "boundary_start, boundary_end, occurred_at, created_at) "
            "VALUES ('ep-y', 'market', 'ETH/USDT', 'ep', ?, ?, ?, ?)",
            (T0, T0, T0, T0),
        )
        await conn.execute(
            "INSERT INTO research_evidence (id, episode_id, mode, payload_json, "
            "observed_at, created_at) VALUES ('ev-lone', 'ep-y', 'RECORDED', "
            "'{}', ?, ?)",
            (T0, T0),
        )
    result = await collect_changes(db, now=NOW)
    t3 = [c for c in result["changes"] if c["tier"] == 3]
    ids = {c["id"] for c in t3}
    assert "ev-1" in ids
    assert "ev-lone" not in ids
    ev = next(c for c in t3 if c["id"] == "ev-1")
    assert ev["epistemic"]["evidence_mode"] == "RECORDED"


# ---- 排序 / 裁剪 / consequence 纪律 ---------------------------------------


@pytest.mark.asyncio
async def test_ordering_tier_asc_time_desc_and_limit_after_sort(db):
    async with db.connection() as conn:
        await _seed_question(conn, "q-1")
        await _seed_revision(
            conn, "rev-a", "q-1", 1, created_at="2026-09-02T00:00:00+00:00"
        )
        await _seed_revision(
            conn, "rev-b", "q-1", 2, created_at="2026-09-03T00:00:00+00:00"
        )
        await _seed_event(
            conn,
            "e-1",
            "q-1",
            "investigate_perspective",
            occurred_at="2026-09-02T00:00:00+00:00",
            actor="selene",
        )
        await _seed_event(
            conn,
            "e-2",
            "q-1",
            "investigate_perspective",
            occurred_at="2026-09-04T00:00:00+00:00",
            actor="selene",
        )
    result = await collect_changes(db, now=NOW, limit=3)
    # 全量 t2+t3 = 4 条,limit=3 裁剪发生在排序后:tier2 两条全保,t3 只剩最新一条
    assert len(result["changes"]) == 3
    tiers = [c["tier"] for c in result["changes"]]
    assert tiers == [2, 2, 3]
    t2_times = [c["occurred_at"] for c in result["changes"][:2]]
    assert t2_times == sorted(t2_times, reverse=True)
    assert result["counts"]["t2"] == 2
    assert result["counts"]["t3"] == 2


@pytest.mark.asyncio
async def test_tier1_not_crowded_out_by_tier3_flood(db):
    async with db.connection() as conn:
        await _seed_question(conn, "q-parent")
        await _seed_question(
            conn, "q-prop", created_by="selene", parent_question_id="q-parent"
        )
        for i in range(5):
            await _seed_event(
                conn,
                f"e-{i}",
                "q-parent",
                "investigate_perspective",
                occurred_at=f"2026-09-0{i + 1}T00:00:00+00:00",
                actor="selene",
            )
    result = await collect_changes(db, now=NOW, limit=3)
    # tier1 排最前,limit 再小也不被 tier3 洪泛挤出
    assert result["changes"][0]["tier"] == 1
    assert result["changes"][0]["source"] == "proposal"


@pytest.mark.asyncio
async def test_decision_scan_limit_500_not_default_50(db):
    """>50 条历史 decision 时到期信号不漏(NIT 修复验证:显式传大 limit)。"""
    async with db.connection() as conn:
        await _seed_question(conn, "q-1")
        for i in range(60):
            await _seed_decision(
                conn,
                f"dec-{i:03d}",
                "q-1",
                window_end="2026-09-05T00:00:00+00:00",
                decided_at=f"2026-08-{i % 28 + 1:02d}T00:00:00+00:00",
            )
    result = await collect_changes(db, now=NOW)
    t1 = [c for c in result["changes"] if c["tier"] == 1]
    assert len(t1) == 60


@pytest.mark.asyncio
async def test_consequence_no_causal_language(db):
    """consequence 是 DB 当前值的模板,不许出现因果脑补词。"""
    async with db.connection() as conn:
        await _seed_question(conn, "q-1", title="甲")
        await _seed_revision(conn, "rev-1", "q-1", 1)
        await _seed_event(conn, "e-1", "q-1", "reopened", detail='{"reason": "r"}')
        await _seed_decision(conn, "dec-1", "q-1")
    result = await collect_changes(db, now=NOW)
    banned = ("因为", "导致", "因此", "由于", "所以")
    for change in result["changes"]:
        for word in banned:
            assert (
                word not in change["consequence"]
            ), f"{change['id']}: {change['consequence']}"


@pytest.mark.asyncio
async def test_since_none_returns_full_tier23(db):
    async with db.connection() as conn:
        await _seed_question(conn, "q-1")
        await _seed_revision(
            conn, "rev-1", "q-1", 1, created_at="2020-01-01T00:00:00+00:00"
        )
    result = await collect_changes(db, now=NOW, since=None)
    assert any(c["tier"] == 2 for c in result["changes"])


@pytest.mark.asyncio
async def test_cites_evidence_only_for_ev_ep_prefix(db):
    async with db.connection() as conn:
        await _seed_question(conn, "q-1")
        await _seed_question(conn, "q-2")
        await _seed_revision(conn, "rev-1", "q-1", 1, cited='["ev-1"]')
        await _seed_revision(conn, "rev-2", "q-1", 2, cited='["rev-1"]')
        await _seed_revision(conn, "rev-3", "q-2", 1, cited='["hyp-9"]')
    result = await collect_changes(db, now=NOW)
    by_id = {c["id"]: c for c in result["changes"]}
    assert by_id["rev-1"]["epistemic"]["cites_evidence"] is True
    assert by_id["rev-2"]["epistemic"]["cites_evidence"] is False
    assert by_id["rev-3"]["epistemic"]["cites_evidence"] is False

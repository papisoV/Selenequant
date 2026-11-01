"""自动续研测试:⑤→① 心跳必须真的接上,且每一步都可停、可查、不越红线。

覆盖:
- 只续研**结案派生**的问题(council 提议的【新问题】没有 derived_from 边 → 不动)
- 已有 revision / 已续研过 → 不重复花钱
- 人手点的调查正在跑 → 让路(共用 investigation_guard)
- 调查会崩溃 → 记失败、不假装 renewed,且不会下一轮再花钱
- 自动开立:主体可映射则建 investigation mission,<明确不建 pillar>
- 主体不可映射(探针 id)→ 跳过并留痕,绝不猜
- autorenew_loop:设置关闭时空转不查库
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio

from app.core import investigation_guard
from app.core.research_autorenew import (
    autorenew_loop,
    autorenew_round,
    promote_one_question,
    renew_one_question,
)

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.mission_thesis import MissionThesisRepository
from selene_space_storage.repositories.research_graph import (
    ResearchEdgeRepository,
    ResearchQuestionRepository,
)
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


class _Settings:
    """只有 ``get_research_autorenew`` 的最小设置替身。"""

    def __init__(self, **overrides: Any) -> None:
        self._section = {
            "enabled": True,
            "interval_minutes": 30,
            "max_per_round": 3,
            "auto_promote": True,
            **overrides,
        }

    def get_research_autorenew(self) -> dict[str, Any]:
        return dict(self._section)


async def _child_question(
    db, *, anchor: dict | None = None, derived: bool = True
) -> tuple[str, str]:
    """父问题 + 结案派生子问题(可选加 derived_from 边)。"""
    repo = ResearchQuestionRepository(db)
    parent_id = await repo.create(
        "BTC/USDT 1h 上 rsi_ma_delta 是否具备可持续的边缘",
        anchor=anchor or {"intervals": ["1h"], "symbols": ["BTC/USDT"]},
        created_by="director",
    )
    child_id = await repo.create(
        "换到 4h 后这个边缘还在吗",
        anchor=anchor or {"intervals": ["1h"], "symbols": ["BTC/USDT"]},
        created_by="selene",
        parent_question_id=parent_id,
    )
    if derived:
        await ResearchEdgeRepository(db).add_fact_edge(
            ("question", child_id),
            ("question", parent_id),
            "derived_from",
            actor="system",
        )
    return parent_id, child_id


async def _fake_council(record: list[str] | None = None):
    """返回一个写 revision、记录调用的假调查会。"""

    async def run(db, settings_service, question_id: str):
        if record is not None:
            record.append(question_id)
        repo = ResearchQuestionRepository(db)
        revision = await repo.add_revision(
            question_id,
            "【时间线】d0=-0.006\n【反向检验】|d1|/|d0|>=0.5 则推翻\n【新问题】1. 换 4h 再看",
        )
        return {
            "revision": revision,
            "citation_migration": {},
            "council": {"perspectives": [], "new_questions": []},
            "question_id": question_id,
        }

    return run


# -- 闸门 ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_only_touches_settlement_derived_questions(db):
    """council 自己提议的【新问题】没有 derived_from 边 → 不该自动续研。"""
    _parent, with_edge = await _child_question(db, derived=True)
    _p2, without_edge = await _child_question(db, derived=False)
    calls: list[str] = []

    counts = await autorenew_round(
        db, _Settings(), council_fn=await _fake_council(calls)
    )

    assert calls == [with_edge]
    assert without_edge not in calls
    assert counts["renewed"] == 1
    assert counts["scanned"] == 1


@pytest.mark.asyncio
async def test_round_skips_question_that_already_has_revision(db):
    _parent, child = await _child_question(db)
    repo = ResearchQuestionRepository(db)
    await repo.add_revision(child, "人手跑过的一版")
    calls: list[str] = []

    counts = await autorenew_round(
        db, _Settings(), council_fn=await _fake_council(calls)
    )

    assert calls == []
    assert counts["scanned"] == 0
    assert counts["renewed"] == 0


@pytest.mark.asyncio
async def test_second_round_does_not_pay_twice(db):
    _parent, child = await _child_question(db)
    calls: list[str] = []
    council = await _fake_council(calls)

    first = await autorenew_round(db, _Settings(), council_fn=council)
    second = await autorenew_round(db, _Settings(), council_fn=council)

    assert calls == [child]
    assert first["renewed"] == 1
    assert second["scanned"] == 0
    assert second["renewed"] == 0


@pytest.mark.asyncio
async def test_running_manual_investigation_makes_autorenew_yield(db):
    """人手点的调查在跑时,自动续研必须让路 —— 共用同一把锁。"""
    _parent, child = await _child_question(db)
    calls: list[str] = []

    task = asyncio.create_task(asyncio.sleep(30))
    investigation_guard.register(child, task)
    try:
        result = await renew_one_question(
            db,
            _Settings(),
            {"id": child},
            _parent,
            council_fn=await _fake_council(calls),
        )
    finally:
        investigation_guard.forget(child)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert result["status"] == "skipped_running"
    assert calls == []


@pytest.mark.asyncio
async def test_council_failure_is_recorded_and_not_retried(db):
    _parent, child = await _child_question(db)
    calls: list[str] = []

    async def boom(db, settings_service, question_id):
        calls.append(question_id)
        raise RuntimeError("LLM 503")

    first = await autorenew_round(db, _Settings(), council_fn=boom)
    second = await autorenew_round(db, _Settings(), council_fn=boom)

    assert calls == [child], "失败后第二轮不该再花钱"
    assert first["failed"] == 1
    assert second["scanned"] == 0

    events = await ResearchQuestionRepository(db).list_events(
        child, kind="autorenew_failed"
    )
    assert len(events) == 1
    assert "RuntimeError" in events[0]["detail"]["error"]


# -- 开立 ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_creates_investigation_mission_without_pillar(db):
    """开立只建 mission;**不建 pillar** —— 留给 mission_verifier 按实际参数生成。"""
    _parent, child = await _child_question(db)
    repo = ResearchQuestionRepository(db)

    counts = await autorenew_round(db, _Settings(), council_fn=await _fake_council())

    assert counts["renewed"] == 1
    assert counts["promoted"] == 1

    revisions = await repo.list_revisions(child)
    assert len(revisions) == 1
    question = await repo.get(child)
    assert question is not None
    missions = await ResearchMissionRepository(db).list_missions(limit=10)
    assert len(missions) == 1
    mission = missions[0]
    assert mission["phase"] == "investigation"
    assert mission["subject_type"] == "market"
    assert mission["subject_id"] == "BTC/USDT"
    assert mission["context"]["pipeline"] == "autorenew"
    assert mission["context"]["question_id"] == child
    assert str(mission["context"]["revision_id"]) == str(revisions[0]["id"])
    assert await MissionThesisRepository(db).list_pillars(str(mission["id"])) == []

    # addresses 边接上,worker 才能顺着 question_id 取回 anchor。
    edges = await ResearchEdgeRepository(db).list_edges(
        str(mission["id"]), direction="out", kind="addresses"
    )
    assert [e["to_id"] for e in edges] == [child]


@pytest.mark.asyncio
async def test_promote_downgrades_probe_id_to_anchor_symbol_and_records_it(db):
    """anchor 挂探针名(生产库里真有),但同一 anchor 有合法标的 → 降级为
    market 主体并留痕。这与父亲 mission 的实际主体一致,不是猜。"""
    _parent, child = await _child_question(
        db,
        anchor={
            "intervals": ["1h"],
            "strategy_ids": ["entry-delay-ic-probe"],
            "symbols": ["BTC/USDT"],
        },
    )

    counts = await autorenew_round(db, _Settings(), council_fn=await _fake_council())

    assert counts["renewed"] == 1
    assert counts["promoted"] == 1

    mission = (await ResearchMissionRepository(db).list_missions(limit=10))[0]
    assert mission["subject_type"] == "market"
    assert mission["subject_id"] == "BTC/USDT"

    events = await ResearchQuestionRepository(db).list_events(
        child, kind="autopromote_done"
    )
    downgrade = events[0]["detail"]["subject_downgrade"]
    assert downgrade is not None
    assert "entry-delay-ic-probe" in downgrade["from"]
    assert "market/BTC/USDT" in events[0]["detail"]["subject_reason"]


@pytest.mark.asyncio
async def test_promote_skips_when_no_usable_subject_at_all(db):
    """既无可映射策略、anchor 里也没有标的 → 跳过并留痕,绝不猜一个主体。"""
    _parent, child = await _child_question(
        db, anchor={"intervals": ["1h"], "strategy_ids": ["entry-delay-ic-probe"]}
    )

    counts = await autorenew_round(db, _Settings(), council_fn=await _fake_council())

    assert counts["renewed"] == 1
    assert counts["promoted"] == 0
    assert counts["skipped"] == 1
    assert await ResearchMissionRepository(db).list_missions(limit=10) == []

    events = await ResearchQuestionRepository(db).list_events(
        child, kind="autopromote_skipped"
    )
    assert len(events) == 1
    assert "entry-delay-ic-probe" in events[0]["detail"]["reason"]


@pytest.mark.asyncio
async def test_promote_records_subject_reason_for_audit(db):
    _parent, child = await _child_question(db)
    fresh = await ResearchQuestionRepository(db).get(child)
    assert fresh is not None
    revision = await ResearchQuestionRepository(db).add_revision(child, "【时间线】x")

    result = await promote_one_question(db, fresh, revision)

    assert result["status"] == "promoted"
    events = await ResearchQuestionRepository(db).list_events(
        child, kind="autopromote_done"
    )
    assert events[0]["detail"]["subject_reason"]


@pytest.mark.asyncio
async def test_auto_promote_off_leaves_revision_but_no_mission(db):
    _parent, child = await _child_question(db)

    counts = await autorenew_round(
        db, _Settings(auto_promote=False), council_fn=await _fake_council()
    )

    assert counts["renewed"] == 1
    assert counts["promoted"] == 0
    assert await ResearchMissionRepository(db).list_missions(limit=10) == []
    assert len(await ResearchQuestionRepository(db).list_revisions(child)) == 1


# -- 循环与红线 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_is_inert_when_disabled(db):
    """默认关闭时循环不碰库 —— 用一个会炸的 db 替身证明它确实没被查。"""
    calls: list[str] = []

    class _ExplodingDb:
        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            calls.append("execute")
            raise AssertionError("disabled loop must not touch storage")

    settings = _Settings(enabled=False)
    state = {"research_db": _ExplodingDb(), "settings_service": settings}

    task = asyncio.create_task(autorenew_loop(state, default_interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls == []


@pytest.mark.asyncio
async def test_round_is_inert_when_settings_disabled(db):
    """直接调 round 也要认关闭 —— 否则"关闭"只是调度层的一个建议。"""
    _parent, _child = await _child_question(db)

    class _ExplodingDb:
        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("disabled round must not touch storage")

    counts = await autorenew_round(_ExplodingDb(), _Settings(enabled=False))

    assert counts["scanned"] == 0
    assert counts["renewed"] == 0


@pytest.mark.asyncio
async def test_autorenew_never_resolves_or_closes_questions(db):
    """红线:自动路径不结案。跑完一整轮,问题仍是 open。"""
    _parent, child = await _child_question(db)

    await autorenew_round(db, _Settings(), council_fn=await _fake_council())

    question = await ResearchQuestionRepository(db).get(child)
    assert question is not None
    assert question["status"] == "open"
    assert question["closed_at"] is None

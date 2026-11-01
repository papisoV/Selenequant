"""POST /missions/{id}/decision 的 resolve 分支 —— 第 ⑤ 步的 API 契约。

``settle_mission`` 单测已证明 core 行为;这里证明**端点真的调了它**,
并且把产物回给调用方(界面要能看见"这次结案带了什么")。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import dependencies as deps

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import (
    ResearchQuestionRepository,
)
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def ctx():
    from app.main import app

    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as mgr:
        deps.set_db_manager(mgr)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c, mgr
        deps.set_db_manager(None)


async def _mission_in_validation(mgr, *, with_question: bool = True):
    question_id = None
    if with_question:
        question_id = await ResearchQuestionRepository(mgr).create(
            title="BTC 1h 上 rsi_ma_delta 是否有可持续边缘?",
            anchor={"symbols": ["BTC/USDT"], "intervals": ["1h"]},
        )
    repo = ResearchMissionRepository(mgr)
    mission_id = await repo.create(
        title="验证 1h 边缘",
        question="BTC 1h 上 rsi_ma_delta 是否有可持续边缘?",
        subject_type="market",
        subject_id="BTC/USDT",
        created_by="user",
        context=({"question_id": question_id} if question_id else {}),
    )
    await repo.transition(mission_id, "validation", actor="user", note="批准验证")
    return mission_id, question_id


@pytest.mark.asyncio
async def test_resolve_writes_decision_child_question_and_revisit(ctx) -> None:
    client, mgr = ctx
    mission_id, question_id = await _mission_in_validation(mgr)
    assert question_id is not None

    resp = await client.post(
        f"/api/v1/research/missions/{mission_id}/decision",
        json={
            "action": "resolve",
            "capture": "测试",
            "outcome": {
                "verdict": "SUPPORTED",
                "rationale": "Sharpe 门槛通过",
                "next_question": "扩大到 4h 是否仍成立?",
                "window_end": "2026-12-01T00:00:00+00:00",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mission"]["phase"] == "resolved"

    settlement = body["settlement"]
    assert settlement["decision_id"]
    assert settlement["child_question_id"]
    assert settlement["revisit_at"]
    assert settlement["window_end"] == "2026-12-01T00:00:00+00:00"

    rows = await mgr.execute(
        "SELECT question_id, verdict, decided_by FROM research_decisions WHERE id = ?",
        (settlement["decision_id"],),
    )
    assert rows[0]["question_id"] == question_id
    assert rows[0]["verdict"] == "SUPPORTED"
    assert rows[0]["decided_by"] == "director"

    child = await ResearchQuestionRepository(mgr).get(settlement["child_question_id"])
    assert child is not None
    assert child["parent_question_id"] == question_id
    assert child["created_by"] == "selene"

    parent = await ResearchQuestionRepository(mgr).get(question_id)
    assert parent is not None
    assert parent["next_revisit_at"] == settlement["revisit_at"]

    # 结案详情里也要有这一笔(活动流可审计)
    mission = await ResearchMissionRepository(mgr).get(mission_id)
    assert mission is not None
    kinds = [
        a["kind"]
        for a in await ResearchMissionRepository(mgr).list_activity(mission_id)
    ]
    assert "phase_transition" in kinds


@pytest.mark.asyncio
async def test_resolve_without_question_succeeds_but_reports_warning(ctx) -> None:
    """老 mission 没挂问题 —— 结案不能被锁死,但缺决策要说出来。"""
    client, mgr = ctx
    mission_id, _ = await _mission_in_validation(mgr, with_question=False)

    resp = await client.post(
        f"/api/v1/research/missions/{mission_id}/decision",
        json={"action": "resolve", "outcome": {"verdict": "PARKED"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mission"]["phase"] == "resolved"
    assert body["settlement"]["decision_id"] is None
    assert any("question_id" in w for w in body["settlement"]["warnings"])


@pytest.mark.asyncio
async def test_non_resolve_actions_carry_no_settlement(ctx) -> None:
    client, mgr = ctx
    mission_id, _ = await _mission_in_validation(mgr)
    repo = ResearchMissionRepository(mgr)
    # approve 走 needs_input → validation;反过来从 validation 出发不合法,
    # 所以另建一条 needs_input 的 mission 来测非 resolve 动作。
    from_validation = await repo.transition(
        mission_id, "needs_input", actor="user", note="回到待决策"
    )
    assert from_validation["phase"] == "needs_input"

    resp = await client.post(
        f"/api/v1/research/missions/{mission_id}/decision",
        json={"action": "more_evidence", "note": "需要更多样本"},
    )
    assert resp.status_code == 200, resp.text
    assert "settlement" not in resp.json()

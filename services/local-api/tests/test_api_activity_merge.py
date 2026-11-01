"""GET /research/agent-activity 的 recent_events 必须是两路合并流。

缺陷形态:端点只读 ``research_question_events``,而那种事件只在"跑调查会"
时才写。生产库实测该表最后一条停在 2026-10-15,同一库的 ``mission_activity``
写到当天 16:59 —— TODAY 页的"最近动作"整段停摆 6 天,而 worker 的每一次
推进、每一次阶段转移都在另一张表里安静地发生。

测试刻意构造"question 侧全是旧事件、mission 侧有最新事件"的形状,只读单表
的实现会返回空/陈旧流。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.core import dependencies as deps
from app.main import app
from httpx import ASGITransport, AsyncClient

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import (
    ResearchQuestionRepository,
)
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client_and_db():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        deps.set_db_manager(db)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, db
        deps.set_db_manager(None)


async def _mission(db: AsyncSQLiteManager, mission_id: str, title: str) -> None:
    await db.execute(
        """
        INSERT INTO research_missions
            (id, title, question, subject_type, subject_id, phase, stage,
             created_by, opened_at, updated_at)
        VALUES (?, ?, 'q?', 'strategy', 'st-1', 'investigation', 'opened',
                'selene', '2026-10-20T00:00:00+00:00', '2026-10-20T00:00:00+00:00')
        """,
        (mission_id, title),
    )


async def test_mission_activity_reaches_the_aggregate_feed(client_and_db):
    """mission 侧的最新活动必须出现在 recent_events 里。"""
    client, db = client_and_db
    await _mission(db, "mis-new", "PA diagnosis BTC/USDT 1h")
    await ResearchMissionRepository(db).append_activity(
        "mis-new",
        actor="selene",
        kind="worker_analysis",
        detail={"recommendation": "more_evidence", "reason": "证据不足"},
    )

    body = (await client.get("/api/v1/research/agent-activity")).json()
    kinds = [e["kind"] for e in body["recent_events"]]
    assert "worker_analysis" in kinds

    ev = next(e for e in body["recent_events"] if e["kind"] == "worker_analysis")
    assert ev["source"] == "mission"
    assert ev["mission_id"] == "mis-new"
    assert ev["question_id"] is None
    # 标题随行带出,前端才不用为每行再查一次 mission 表
    assert ev["question_title"] == "PA diagnosis BTC/USDT 1h"


async def test_both_streams_merge_in_descending_time_order(client_and_db):
    """两路交错时按发生时间降序合并,不是先 A 后 B 的拼接。"""
    client, db = client_and_db
    repo_q = ResearchQuestionRepository(db)
    qid = await repo_q.create(title="SOL 4h 亏损根因")
    await repo_q.append_event(
        qid, actor="selene", kind="investigate_finished", detail={"status": "ok"}
    )
    await _mission(db, "mis-a", "mission A")
    await ResearchMissionRepository(db).append_activity(
        "mis-a", actor="selene", kind="worker_started", detail={"rounds": 0}
    )
    # 再写一条更新的问题事件,它必须排在最前
    await repo_q.append_event(
        qid, actor="selene", kind="investigate_finished", detail={"status": "ok"}
    )

    body = (await client.get("/api/v1/research/agent-activity")).json()
    sources = [e["source"] for e in body["recent_events"]]
    assert "question" in sources and "mission" in sources
    times = [e["occurred_at"] for e in body["recent_events"]]
    assert times == sorted(times, reverse=True)


async def test_question_events_keep_their_projection(client_and_db):
    """question 侧原有契约不变:question_id 仍在,mission_id 为 null。"""
    client, db = client_and_db
    repo = ResearchQuestionRepository(db)
    qid = await repo.create(title="问题一")
    await repo.append_event(
        qid,
        actor="selene",
        kind="investigate_perspective",
        detail={"perspective": "empiricist", "content": "长" * 300, "status": "ok"},
    )

    body = (await client.get("/api/v1/research/agent-activity")).json()
    ev = next(
        e for e in body["recent_events"] if e["kind"] == "investigate_perspective"
    )
    assert ev["source"] == "question"
    assert ev["question_id"] == qid
    assert ev["mission_id"] is None
    assert ev["question_title"] == "问题一"
    # 全文不进聚合流,只留 200 字摘要
    assert len(ev["detail"]["excerpt"]) == 200
    assert "content" not in ev["detail"]

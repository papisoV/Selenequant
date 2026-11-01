"""GET /research/agent-activity 聚合端点契约测试。

真值来源三路:
- running:进程内 _INVESTIGATE_TASKS 注册表(用假 Task 对象注入)
- recent_events:research_question_events 跨问题降序流
- heartbeat:PA 心跳(app.state 未挂调度器时 enabled 默认 False)

不碰真实 LLM:调查会本身不被触发,只验证聚合读面。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.api.v1 import research_graph as rg
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import (
    ResearchQuestionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

pytestmark = pytest.mark.asyncio


class _FakeTask:
    """最小 Task 替身:running_investigations 只问 done()。"""

    def __init__(self, done: bool = False) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


@pytest_asyncio.fixture
async def ctx():
    from app.core import dependencies as deps

    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        # 聚合端点走 _repo() → get_db_manager(),不是依赖覆盖;
        # 与 test_api_mission_create.py 同法挂内存库,teardown 复位。
        deps.set_db_manager(db)
        app = create_app()
        app.dependency_overrides[rg.get_graph_db] = lambda: db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, db, app
        deps.set_db_manager(None)
    # 注册表是模块级全局:测试间必须清,否则假 Task 泄漏到别的用例
    rg._INVESTIGATE_TASKS.clear()
    rg._INVESTIGATE_STARTED_AT.clear()


async def _new_question(db, title: str = "SOL 4h 亏损根因") -> str:
    return await ResearchQuestionRepository(db).create(title=title)


async def test_empty_state_reports_honest_empties(ctx):
    client, _db, _app = ctx
    resp = await client.get("/api/v1/research/agent-activity")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["running"] == []
    # 新库里至少有问题创建事件?—— 没有建问题,应为空
    assert body["recent_events"] == []
    assert body["heartbeat"]["enabled"] is False


async def test_running_registry_reports_question_title(ctx):
    client, db, _app = ctx
    qid = await _new_question(db, "BTC 4h 动量是否可交易")
    rg._INVESTIGATE_TASKS[qid] = _FakeTask()
    rg._INVESTIGATE_STARTED_AT[qid] = "2026-10-20T01:00:00+00:00"

    body = (await client.get("/api/v1/research/agent-activity")).json()
    assert len(body["running"]) == 1
    run = body["running"][0]
    assert run["question_id"] == qid
    assert run["question_title"] == "BTC 4h 动量是否可交易"
    assert run["started_at"] == "2026-10-20T01:00:00+00:00"


async def test_finished_task_is_not_reported_as_running(ctx):
    client, db, _app = ctx
    qid = await _new_question(db)
    rg._INVESTIGATE_TASKS[qid] = _FakeTask(done=True)
    rg._INVESTIGATE_STARTED_AT[qid] = "2026-10-20T01:00:00+00:00"

    body = (await client.get("/api/v1/research/agent-activity")).json()
    assert body["running"] == []


async def test_perspective_events_are_excerpted_and_ordered_newest_first(ctx):
    client, db, _app = ctx
    repo = ResearchQuestionRepository(db)
    q1 = await repo.create(title="问题一")
    q2 = await repo.create(title="问题二")
    long_content = "甲" * 500
    await repo.append_event(
        q1,
        actor="selene",
        kind="investigate_perspective",
        detail={
            "perspective": "empiricist",
            "content": long_content,
            "cited": ["ev-1", "ev-2"],
            "status": "ok",
        },
    )
    await repo.append_event(
        q2,
        actor="selene",
        kind="investigate_perspective",
        detail={
            "perspective": "challenger",
            "content": "短内容",
            "cited": [],
            "status": "failed",
        },
    )

    body = (await client.get("/api/v1/research/agent-activity")).json()
    # created 事件同在该窗口;这里只钉视角事件本身的投影
    events = [
        e for e in body["recent_events"] if e["kind"] == "investigate_perspective"
    ]
    assert len(events) == 2
    # 降序:后写的那条在前
    assert events[0]["question_id"] == q2
    assert events[0]["question_title"] == "问题二"
    assert events[0]["detail"]["excerpt"] == "短内容"
    assert events[0]["detail"]["truncated"] is False

    assert events[1]["question_id"] == q1
    assert events[1]["detail"]["perspective"] == "empiricist"
    assert events[1]["detail"]["cited_count"] == 2
    assert len(events[1]["detail"]["excerpt"]) == 200
    assert events[1]["detail"]["truncated"] is True
    # 全文不进聚合流
    assert "content" not in events[1]["detail"]


async def test_unrelated_event_kinds_are_filtered_out(ctx):
    client, db, _app = ctx
    repo = ResearchQuestionRepository(db)
    qid = await repo.create(title="只有创建事件")
    await repo.append_event(
        qid,
        actor="user",
        kind="unknown_custom_event",
        detail={"x": 1},
    )
    body = (await client.get("/api/v1/research/agent-activity")).json()
    kinds = {e["kind"] for e in body["recent_events"]}
    assert "unknown_custom_event" not in kinds
    # created 事件本身在窗口内
    assert "created" in kinds

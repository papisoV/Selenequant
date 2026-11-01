"""批次3 人工介入:investigate guidance / 取消 / mission 留言。

- guidance:注入三视角 + 主持人 prompt;事件/回答 rationale 留痕
- cancel:在跑 → 409 以外阻断、真叫停后落 investigate_finished status=cancelled
- mission note:进活动流 actor=user kind=user_note,不改 phase
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from app.api.v1 import research_graph as rg
from app.services import investigate_council as council
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import (
    ResearchQuestionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def ctx():
    from app.core import dependencies as deps

    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        deps.set_db_manager(db)
        app = create_app()
        app.dependency_overrides[rg.get_graph_db] = lambda: db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, db
        deps.set_db_manager(None)
    rg._INVESTIGATE_TASKS.clear()
    rg._INVESTIGATE_STARTED_AT.clear()


async def _await_investigate() -> None:
    tasks = list(rg._INVESTIGATE_TASKS.values())
    rg._INVESTIGATE_TASKS.clear()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _make_question(client: AsyncClient, title: str = "SOL 4h 亏损根因") -> str:
    resp = await client.post(
        "/api/v1/research/graph/questions",
        json={"title": title},
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["question_id"])


# ---------------------------------------------------------------------------
# guidance
# ---------------------------------------------------------------------------


async def test_guidance_reaches_all_three_perspectives_and_host(ctx):
    client, _db = ctx
    qid = await _make_question(client)
    prompts: list[str] = []

    async def _fake_llm(settings_service, prompt, **kwargs):
        prompts.append(prompt)
        return "分析结论。"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(
            f"/api/v1/research/graph/questions/{qid}/investigate",
            json={"guidance": "重点看资金费率与持仓量,不要泛泛谈趋势"},
        )
        assert resp.status_code == 202, resp.text
        await _await_investigate()
    finally:
        monkeypatch.undo()

    assert len(prompts) == 4  # 3 视角 + 1 主持人
    for p in prompts:
        assert "重点看资金费率与持仓量" in p
    # 主持人多了"必须显式回应"的指令
    assert "指示未被覆盖" in prompts[-1]


async def test_guidance_is_recorded_in_event_and_revision_rationale(ctx):
    client, db = ctx
    qid = await _make_question(client)

    async def _fake_llm(settings_service, prompt, **kwargs):
        return "结论。"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        await client.post(
            f"/api/v1/research/graph/questions/{qid}/investigate",
            json={"guidance": "只看 4h"},
        )
        await _await_investigate()
    finally:
        monkeypatch.undo()

    repo = ResearchQuestionRepository(db)
    events = await repo.list_events(qid, kind="investigate_perspective")
    assert events and all(e["detail"].get("guidance") == "只看 4h" for e in events)
    revisions = await repo.list_revisions(qid)
    assert any("user_guidance:只看 4h" in (r.get("rationale") or []) for r in revisions)


async def test_blank_guidance_is_normalized_to_none(ctx):
    client, _db = ctx
    qid = await _make_question(client)
    prompts: list[str] = []

    async def _fake_llm(settings_service, prompt, **kwargs):
        prompts.append(prompt)
        return "结论。"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        await client.post(
            f"/api/v1/research/graph/questions/{qid}/investigate",
            json={"guidance": "   "},
        )
        await _await_investigate()
    finally:
        monkeypatch.undo()

    for p in prompts:
        assert "研究负责人的本轮指示" not in p


async def test_overlong_guidance_is_422(ctx):
    client, _db = ctx
    qid = await _make_question(client)
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/investigate",
        json={"guidance": "x" * 2001},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


async def test_cancel_when_idle_is_409(ctx):
    client, _db = ctx
    qid = await _make_question(client)
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/investigate/cancel"
    )
    assert resp.status_code == 409


async def test_cancel_running_investigation_stops_it_and_marks_cancelled(ctx):
    client, db = ctx
    qid = await _make_question(client)
    started = asyncio.Event()

    async def _slow_llm(settings_service, prompt, **kwargs):
        started.set()
        await asyncio.sleep(3600)  # 被取消前一直挂起
        return "不该到达。"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _slow_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await asyncio.wait_for(started.wait(), timeout=5)

        cancel = await client.post(
            f"/api/v1/research/graph/questions/{qid}/investigate/cancel"
        )
        assert cancel.status_code == 200, cancel.text
        assert cancel.json()["data"]["status"] == "cancelled"
    finally:
        # 取消路径自己等过 task;这里只清注册表残留
        for t in list(rg._INVESTIGATE_TASKS.values()):
            t.cancel()
        rg._INVESTIGATE_TASKS.clear()
        rg._INVESTIGATE_STARTED_AT.clear()
        monkeypatch.undo()

    repo = ResearchQuestionRepository(db)
    events = await repo.list_events(qid, kind="investigate_finished")
    assert len(events) == 1
    assert events[0]["detail"]["status"] == "cancelled"
    assert events[0]["actor"] == "user"
    # 没有 revision:三视角都没产出
    assert await repo.list_revisions(qid) == []


# ---------------------------------------------------------------------------
# mission note
# ---------------------------------------------------------------------------


async def _make_mission(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/v1/research/missions",
        json={
            "title": "验证动量假设",
            "question": "4h 动量有边缘吗?",
            "subject_type": "market",
            "subject_id": "BTC/USDT",
        },
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["mission"]["id"])


async def test_mission_note_appends_user_activity_without_phase_change(ctx):
    client, _db = ctx
    mid = await _make_mission(client)
    before = (await client.get(f"/api/v1/research/missions/{mid}")).json()["mission"]

    resp = await client.post(
        f"/api/v1/research/missions/{mid}/note",
        json={"note": "注意排除资金费率突变的影响"},
    )
    assert resp.status_code == 200, resp.text
    after = resp.json()["mission"]
    assert after["phase"] == before["phase"]

    acts = (await client.get(f"/api/v1/research/missions/{mid}/activity")).json()[
        "activity"
    ]
    notes = [a for a in acts if a["kind"] == "user_note"]
    assert len(notes) == 1
    assert notes[0]["actor"] == "user"
    assert notes[0]["detail"]["note"] == "注意排除资金费率突变的影响"


async def test_mission_note_blank_is_422(ctx):
    client, _db = ctx
    mid = await _make_mission(client)
    resp = await client.post(
        f"/api/v1/research/missions/{mid}/note", json={"note": "   "}
    )
    assert resp.status_code == 422


async def test_mission_note_unknown_mission_is_404(ctx):
    client, _db = ctx
    resp = await client.post(
        "/api/v1/research/missions/mis-doesnotexist/note", json={"note": "x"}
    )
    assert resp.status_code == 404

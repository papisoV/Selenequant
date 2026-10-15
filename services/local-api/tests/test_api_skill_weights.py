"""``GET /decision-signals/skill-weights`` —— 信封、过滤参数、路由不被遮蔽(📋3)。

与 ``test_api_decision_signals.py`` 同法:``dependency_overrides`` 换内存库。
这里只锁**路由层**(信封 + 参数透传 + 与 ``/{signal_id}`` 的顺序),装配层的
口径在 ``test_decision_signal_skill_weights_service.py``。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.api.v1.decision_signals import get_signals_db
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.decision_signals import (
    DecisionSignalRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

pytestmark = pytest.mark.asyncio

ENGINE = "decision-signal-v1"


async def _client(db) -> AsyncClient:
    app = create_app()
    app.dependency_overrides[get_signals_db] = lambda: db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as mgr:
        yield mgr


async def _seed(db, *, agent, outcome, count=1, report="rep-api-sw"):
    repo = DecisionSignalRepository(db)
    for i in range(count):
        signal_id, _ = await repo.create_if_absent(
            {
                "stock_code": "600519",
                "market": "cn",
                "source_type": "report",
                "source_report_id": f"{report}-{i}",
                "source_agent": agent,
                "trigger_source": "post_market",
                "action": "sell",
                "horizon": "5d",
                "plan_quality": "ok",
                "status": "active",
            }
        )
        await repo.upsert_outcome(
            signal_id,
            {
                "horizon": "5d",
                "engine_version": ENGINE,
                "source_agent": agent,
                "eval_status": "completed",
                "outcome": outcome,
                "direction_correct": 1 if outcome == "hit" else 0,
            },
        )


async def test_empty_db_returns_envelope_with_no_skills(db):
    async with await _client(db) as c:
        resp = await c.get("/api/v1/decision-signals/skill-weights")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["data"]["skills"] == []
    assert body["data"]["engine_version"] == ENGINE
    assert body["data"]["minimum_evaluated_sample_size"] == 30


async def test_weights_endpoint_reflects_seeded_skill(db):
    await _seed(db, agent="alpha", outcome="hit", count=40)
    async with await _client(db) as c:
        resp = await c.get("/api/v1/decision-signals/skill-weights")
    skill = next(s for s in resp.json()["data"]["skills"] if s["skill_id"] == "alpha")
    assert skill["factor"] > 1.0


async def test_skill_id_query_param_filters(db):
    await _seed(db, agent="alpha", outcome="hit", count=40, report="a")
    await _seed(db, agent="beta", outcome="hit", count=40, report="b")
    async with await _client(db) as c:
        resp = await c.get(
            "/api/v1/decision-signals/skill-weights", params={"skill_id": "alpha"}
        )
    ids = [s["skill_id"] for s in resp.json()["data"]["skills"]]
    assert ids == ["alpha"]


async def test_engine_version_query_param_filters(db):
    await _seed(db, agent="alpha", outcome="hit", count=40)
    async with await _client(db) as c:
        resp = await c.get(
            "/api/v1/decision-signals/skill-weights",
            params={"engine_version": "decision-signal-v9"},
        )
    assert resp.json()["data"]["skills"] == []


async def test_weights_route_is_not_shadowed_by_signal_id_route(db):
    # /skill-weights 必须能命中本端点 —— 若被 /{signal_id} 抢先匹配会 422。
    async with await _client(db) as c:
        resp = await c.get("/api/v1/decision-signals/skill-weights")
    assert resp.status_code == 200
    assert "skills" in resp.json()["data"]

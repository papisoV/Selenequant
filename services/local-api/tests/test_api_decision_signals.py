"""决策信号路由 —— 信封、写入口的 action 校验、404、状态推进、统计(📋8)。

用 ``app.dependency_overrides`` 把 ``get_signals_db`` 换成内存库(与
``test_api_account_state.py`` / ``research_graph.py`` 同法),不碰真实 selenespace.db。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.api.v1.decision_signals import get_signals_db
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager

pytestmark = pytest.mark.asyncio


def _payload(**overrides) -> dict:
    base = {
        "stock_code": "600519",
        "action": "sell",
        "market": "cn",
        "source_type": "report",
        "trigger_source": "post_market",
        "source_report_id": "rep-api-1",
        "horizon": "5d",
    }
    base.update(overrides)
    return base


async def _client(db) -> AsyncClient:
    app = create_app()
    app.dependency_overrides[get_signals_db] = lambda: db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as mgr:
        yield mgr


async def test_create_returns_envelope_with_signal_id(db):
    async with await _client(db) as c:
        resp = await c.post("/api/v1/decision-signals", json=_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["data"]["created"] is True
    assert isinstance(body["data"]["signal_id"], int)


async def test_replay_returns_same_id_and_created_false(db):
    async with await _client(db) as c:
        first = await c.post("/api/v1/decision-signals", json=_payload())
        second = await c.post("/api/v1/decision-signals", json=_payload())
    assert first.json()["data"]["signal_id"] == second.json()["data"]["signal_id"]
    assert second.json()["data"]["created"] is False


async def test_unknown_action_is_rejected_with_400(db):
    # 八态之外的动作(含 Selene 的 long/short 词汇)必须在入口被拒 —— 本路由不猜映射。
    async with await _client(db) as c:
        resp = await c.post("/api/v1/decision-signals", json=_payload(action="long"))
    assert resp.status_code == 400
    assert "unknown action" in resp.json()["detail"]


async def test_action_is_normalized_to_lowercase(db):
    async with await _client(db) as c:
        resp = await c.post("/api/v1/decision-signals", json=_payload(action="SELL"))
    assert resp.status_code == 200
    signal_id = resp.json()["data"]["signal_id"]
    async with await _client(db) as c:
        detail = await c.get(f"/api/v1/decision-signals/{signal_id}")
    assert detail.json()["data"]["signal"]["action"] == "sell"


async def test_get_unknown_signal_is_404(db):
    async with await _client(db) as c:
        resp = await c.get("/api/v1/decision-signals/424242")
    assert resp.status_code == 404


async def test_get_signal_returns_outcomes_list(db):
    async with await _client(db) as c:
        created = await c.post("/api/v1/decision-signals", json=_payload())
        signal_id = created.json()["data"]["signal_id"]
        resp = await c.get(f"/api/v1/decision-signals/{signal_id}")
    body = resp.json()["data"]
    assert body["signal"]["stock_code"] == "600519"
    assert body["outcomes"] == []


async def test_list_filters_by_action(db):
    async with await _client(db) as c:
        await c.post("/api/v1/decision-signals", json=_payload(action="sell"))
        await c.post(
            "/api/v1/decision-signals",
            json=_payload(action="buy", source_report_id="rep-api-2"),
        )
        resp = await c.get("/api/v1/decision-signals", params={"action": "buy"})
    signals = resp.json()["data"]["signals"]
    assert [s["action"] for s in signals] == ["buy"]


async def test_status_update_and_unknown_status(db):
    async with await _client(db) as c:
        created = await c.post("/api/v1/decision-signals", json=_payload())
        signal_id = created.json()["data"]["signal_id"]
        ok = await c.post(
            f"/api/v1/decision-signals/{signal_id}/status", json={"status": "archived"}
        )
        bad = await c.post(
            f"/api/v1/decision-signals/{signal_id}/status", json={"status": "vibes"}
        )
        missing = await c.post(
            "/api/v1/decision-signals/999999/status", json={"status": "archived"}
        )
    assert ok.status_code == 200
    assert ok.json()["data"]["status"] == "archived"
    assert bad.status_code == 400
    assert missing.status_code == 404


async def test_stats_endpoint_returns_engine_and_statuses(db):
    async with await _client(db) as c:
        resp = await c.get("/api/v1/decision-signals/stats")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["engine_version"] == "decision-signal-v1"
    assert "archived" not in data["stats_statuses"]
    assert data["total"] == 0


async def test_stats_route_is_not_shadowed_by_signal_id_route(db):
    # /stats 必须能命中统计端点 —— 若被 /{signal_id} 抢先匹配会 422(int 解析失败)。
    async with await _client(db) as c:
        resp = await c.get("/api/v1/decision-signals/stats")
    assert resp.status_code == 200
    assert "hit_rate_pct" in resp.json()["data"]

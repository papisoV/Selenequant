"""透明性批3-12: record_system_event 复活 events_recent 死表。

写入方(任务完成/漂移核查/心跳异常)共用 fire-and-forget helper;
本测试直接调 helper + GET /events/recent 对账(同库可读回)。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def client():
    from selene_space_storage.migrations import MigrationRunner
    from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
    from selene_space_storage.sqlite import AsyncSQLiteManager

    from app.core import dependencies as deps
    from app.main import app

    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as mgr:
        deps.set_db_manager(mgr)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c, mgr
        deps.set_db_manager(None)


@pytest.mark.asyncio
async def test_record_system_event_persists_and_readback(client):
    c, _mgr = client
    from app.api.v1.events import record_system_event

    await record_system_event(
        "task_completed",
        "backtest",
        "回测完成 BTC/USDT 1d (bb_v3)",
        severity="info",
        symbol="BTC/USDT",
        payload={"task_id": "bt-1", "total_return": 3.2},
    )
    resp = await c.get("/api/v1/events/recent")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "task_completed"
    assert row["source"] == "backtest"
    assert row["title"] == "回测完成 BTC/USDT 1d (bb_v3)"
    assert row["severity"] == "info"
    assert "task_id" in row["payload_json"]


@pytest.mark.asyncio
async def test_record_system_event_db_none_is_silent(client):
    c, _mgr = client
    from app.core import dependencies as deps
    from app.api.v1.events import record_system_event

    deps.set_db_manager(None)
    # 不抛异常即通过(fire-and-forget 契约)
    await record_system_event("drift_check", "research", "x")


@pytest.mark.asyncio
async def test_drift_check_writes_system_event(client):
    """漂移核查端点 → events_recent 出现 drift_check 行(端到端)。"""
    c, mgr = client
    # 先建一个 mission
    resp = await c.post(
        "/api/v1/research/missions",
        json={
            "title": "验证 RSI 背离",
            "question": "有边缘吗?",
            "subject_type": "strategy",
            "subject_id": "strat-x",
        },
    )
    assert resp.status_code == 200, resp.text
    mid = resp.json()["mission"]["id"]

    resp = await c.post(
        f"/api/v1/research/missions/{mid}/drift-check",
        json={
            "patterns": {
                p: {"present": False}
                for p in (
                    "reason_changed",
                    "catalyst_laundering",
                    "valuation_substitution",
                    "dismissal_by_horizon",
                    "protection_as_reason",
                )
            },
            "checked_by": "director",
            "note": "n",
        },
    )
    assert resp.status_code == 200, resp.text

    resp = await c.get("/api/v1/events/recent?kind=drift_check")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) == 1
    assert rows[0]["severity"] == "warning"

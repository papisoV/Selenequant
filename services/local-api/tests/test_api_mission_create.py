"""透明性批3-10: POST /missions 创建端点契约测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import dependencies as deps

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def client():
    from app.main import app

    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as mgr:
        deps.set_db_manager(mgr)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
        deps.set_db_manager(None)


@pytest.mark.asyncio
async def test_create_mission_returns_mission_in_investigation(client):
    resp = await client.post(
        "/api/v1/research/missions",
        json={
            "title": "验证 RSI 背离假设",
            "question": "BTC 4h 的 RSI 背离信号是否有可交易边缘?",
            "subject_type": "market",
            "subject_id": "BTC/USDT",
        },
    )
    assert resp.status_code == 200
    mission = resp.json()["mission"]
    assert mission["title"] == "验证 RSI 背离假设"
    assert mission["phase"] == "investigation"
    assert mission["created_by"] == "user"
    assert mission["id"].startswith("mis-")


@pytest.mark.asyncio
async def test_create_mission_invalid_subject_type_422(client):
    resp = await client.post(
        "/api/v1/research/missions",
        json={
            "title": "x",
            "question": "y",
            "subject_type": "galaxy",
            "subject_id": "BTC/USDT",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_mission_seed_activity_present(client):
    resp = await client.post(
        "/api/v1/research/missions",
        json={
            "title": "t",
            "question": "q",
            "subject_type": "system",
            "subject_id": "sys-1",
        },
    )
    mission_id = resp.json()["mission"]["id"]
    act = await client.get(f"/api/v1/research/missions/{mission_id}/activity")
    kinds = [a["kind"] for a in act.json()["activity"]]
    assert "mission_opened" in kinds

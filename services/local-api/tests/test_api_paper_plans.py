"""Tests for PaperPlan API (阶段5 — 记录到 paper 计划).

POST /experiments/{id}/paper-plan 从实验 setup_params 部署计划；
GET /paper-plans 列表；POST /paper-plans/{id}/transition 状态迁移。
两个 repository 都经 dependency_overrides 注入内存实例。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.core.dependencies import get_experiment_repo, get_paper_plan_repo
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.experiments import ExperimentRepository
from selene_space_storage.repositories.paper_plans import PaperPlanRepository
from selene_space_storage.sqlite import AsyncSQLiteManager


def _exp_payload() -> dict:
    return {
        "name": "BTC 1h 双顶形态",
        "hypothesis": "关键阻力位双顶 → 跌破颈线放空有正期望",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "dataset": "klines.db 2026-01-01~2026-07-01",
        "strategy_version": "pa-decision-v1",
        "setup_params": {
            "direction": "short",
            "entry": 65000.0,
            "stop_loss": 66000.0,
            "take_profit": 61000.0,
        },
        "backtest_result": {"total_setups": 12, "win_rate": 0.58, "expectancy": 0.42},
        "evidence_refs": ["conclusion-3"],
    }


@pytest_asyncio.fixture
async def repos():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield ExperimentRepository(db), PaperPlanRepository(db)


@pytest_asyncio.fixture
async def client(repos):
    exp_repo, plan_repo = repos
    app = create_app()
    app.dependency_overrides[get_experiment_repo] = lambda: exp_repo
    app.dependency_overrides[get_paper_plan_repo] = lambda: plan_repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_deploy_from_experiment(client: AsyncClient):
    exp = (await client.post("/api/v1/experiments", json=_exp_payload())).json()
    resp = await client.post(f"/api/v1/experiments/{exp['id']}/paper-plan", json={})
    assert resp.status_code == 200
    plan = resp.json()
    assert plan["experiment_id"] == exp["id"]
    assert plan["symbol"] == "BTC/USDT"
    assert plan["timeframe"] == "1h"
    assert plan["direction"] == "short"
    assert plan["entry"] == 65000.0
    assert plan["stop_loss"] == 66000.0
    assert plan["take_profit"] == 61000.0
    assert plan["quantity"] == 0.001
    assert plan["account_id"] == "paper-default"
    assert plan["status"] == "planned"


@pytest.mark.asyncio
async def test_deploy_with_overrides(client: AsyncClient):
    exp = (await client.post("/api/v1/experiments", json=_exp_payload())).json()
    resp = await client.post(
        f"/api/v1/experiments/{exp['id']}/paper-plan",
        json={"account_id": "my-acc", "quantity": 0.05, "note": "首仓观察"},
    )
    assert resp.status_code == 200
    plan = resp.json()
    assert plan["account_id"] == "my-acc"
    assert plan["quantity"] == 0.05
    assert plan["note"] == "首仓观察"


@pytest.mark.asyncio
async def test_deploy_missing_experiment_404(client: AsyncClient):
    resp = await client.post("/api/v1/experiments/exp-nope/paper-plan", json={})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_deploy_incomplete_setup_422(client: AsyncClient):
    payload = _exp_payload()
    payload["setup_params"] = {"direction": "long"}  # 缺 entry/SL/TP
    exp = (await client.post("/api/v1/experiments", json=payload)).json()
    resp = await client.post(f"/api/v1/experiments/{exp['id']}/paper-plan", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_paper_plans_status_filter(client: AsyncClient):
    exp = (await client.post("/api/v1/experiments", json=_exp_payload())).json()
    await client.post(f"/api/v1/experiments/{exp['id']}/paper-plan", json={})
    await client.post(f"/api/v1/experiments/{exp['id']}/paper-plan", json={"note": "b"})

    resp = await client.get("/api/v1/paper-plans")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert all(r["status"] == "planned" for r in rows)


@pytest.mark.asyncio
async def test_list_by_experiment(client: AsyncClient):
    exp = (await client.post("/api/v1/experiments", json=_exp_payload())).json()
    await client.post(f"/api/v1/experiments/{exp['id']}/paper-plan", json={})

    resp = await client.get(f"/api/v1/experiments/{exp['id']}/paper-plans")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["experiment_id"] == exp["id"]


@pytest.mark.asyncio
async def test_transition_valid_and_invalid(client: AsyncClient):
    exp = (await client.post("/api/v1/experiments", json=_exp_payload())).json()
    plan = (await client.post(f"/api/v1/experiments/{exp['id']}/paper-plan", json={})).json()

    # planned -> active
    resp = await client.post(
        f"/api/v1/paper-plans/{plan['id']}/transition", json={"status": "active"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    # completed -> 不能再跳回 active (非法边 → 400)
    resp = await client.post(
        f"/api/v1/paper-plans/{plan['id']}/transition", json={"status": "completed"}
    )
    assert resp.status_code == 200
    resp = await client.post(
        f"/api/v1/paper-plans/{plan['id']}/transition", json={"status": "cancelled"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_transition_bad_status_400(client: AsyncClient):
    exp = (await client.post("/api/v1/experiments", json=_exp_payload())).json()
    plan = (await client.post(f"/api/v1/experiments/{exp['id']}/paper-plan", json={})).json()
    resp = await client.post(
        f"/api/v1/paper-plans/{plan['id']}/transition", json={"status": "banana"}
    )
    assert resp.status_code == 400

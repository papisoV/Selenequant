"""Tests for Experiment 登记 API (POST/GET /api/v1/experiments, fork).

Uses an in-memory ExperimentRepository injected via dependency_overrides so
the test does not depend on the real lifespan DB.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.core.dependencies import get_experiment_repo
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.experiments import ExperimentRepository
from selene_space_storage.sqlite import AsyncSQLiteManager


def _payload() -> dict:
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
async def repo():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield ExperimentRepository(db)


@pytest_asyncio.fixture
async def client(repo: ExperimentRepository):
    app = create_app()
    app.dependency_overrides[get_experiment_repo] = lambda: repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_returns_id(client: AsyncClient):
    resp = await client.post("/api/v1/experiments", json=_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"].startswith("exp-")
    assert body["name"] == "BTC 1h 双顶形态"


@pytest.mark.asyncio
async def test_get_roundtrip(client: AsyncClient):
    created = (await client.post("/api/v1/experiments", json=_payload())).json()
    resp = await client.get(f"/api/v1/experiments/{created['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["setup_params"] == _payload()["setup_params"]
    assert body["backtest_result"]["win_rate"] == 0.58
    assert body["evidence_refs"] == ["conclusion-3"]


@pytest.mark.asyncio
async def test_get_missing_returns_404(client: AsyncClient):
    resp = await client.get("/api/v1/experiments/exp-nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_newest_first_and_symbol_filter(client: AsyncClient):
    await client.post("/api/v1/experiments", json=_payload())
    eth = _payload()
    eth["symbol"] = "ETH/USDT"
    eth["name"] = "ETH 双顶"
    await client.post("/api/v1/experiments", json=eth)

    resp = await client.get("/api/v1/experiments?symbol=ETH/USDT")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETH/USDT"
    assert rows[0]["name"] == "ETH 双顶"


@pytest.mark.asyncio
async def test_fork_sets_parent_and_overrides(client: AsyncClient):
    created = (await client.post("/api/v1/experiments", json=_payload())).json()
    pid = created["id"]
    resp = await client.post(
        f"/api/v1/experiments/{pid}/fork",
        json={"overrides": {"name": "变异版双顶", "setup_params": {"direction": "long"}}},
    )
    assert resp.status_code == 200
    child = resp.json()
    assert child["id"] != pid
    assert child["parent_experiment"] == pid
    assert child["name"] == "变异版双顶"
    assert child["setup_params"] == {"direction": "long"}
    assert child["hypothesis"] == _payload()["hypothesis"]


@pytest.mark.asyncio
async def test_fork_missing_parent_returns_404(client: AsyncClient):
    resp = await client.post("/api/v1/experiments/exp-nope/fork", json={"overrides": {}})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_evidence_report_composes_all_sections(client: AsyncClient):
    created = (await client.post("/api/v1/experiments", json=_payload())).json()
    resp = await client.get(f"/api/v1/experiments/{created['id']}/evidence-report")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "experiment",
        "data_source",
        "config",
        "version",
        "result",
        "robustness",
        "risk",
        "conclusion",
        "lineage",
    }
    # 核心字段组合自存储实验
    assert body["experiment"]["name"] == "BTC 1h 双顶形态"
    assert body["data_source"]["dataset"] == "klines.db 2026-01-01~2026-07-01"
    assert body["config"]["setup_params"]["direction"] == "short"
    assert body["result"]["win_rate"] == 0.58
    assert body["robustness"]["has_backtest"] is True
    assert body["lineage"]["parent"] is None
    assert body["lineage"]["ancestors"] == []
    assert body["lineage"]["children"] == []


@pytest.mark.asyncio
async def test_evidence_report_includes_lineage(client: AsyncClient):
    parent = (await client.post("/api/v1/experiments", json=_payload())).json()
    child = (
        await client.post(
            f"/api/v1/experiments/{parent['id']}/fork",
            json={"overrides": {"name": "变异版双顶"}},
        )
    ).json()

    resp = await client.get(f"/api/v1/experiments/{child['id']}/evidence-report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lineage"]["parent"] == parent["id"]
    assert body["lineage"]["ancestors"][0]["id"] == parent["id"]
    # parent 视角看到 child 在 children
    parent_report = (await client.get(f"/api/v1/experiments/{parent['id']}/evidence-report")).json()
    assert parent_report["lineage"]["children"][0]["id"] == child["id"]


@pytest.mark.asyncio
async def test_evidence_report_missing_returns_404(client: AsyncClient):
    resp = await client.get("/api/v1/experiments/exp-nope/evidence-report")
    assert resp.status_code == 404

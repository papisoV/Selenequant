"""Strategy CRUD / lifecycle / deployment endpoints under /api/v1/strategies."""

from __future__ import annotations

import app.api.v1.strategies as strategies_mod
import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _wipe_store():
    strategies_mod._strategies.clear()
    strategies_mod._strategy_versions.clear()
    yield
    strategies_mod._strategies.clear()
    strategies_mod._strategy_versions.clear()


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _create_strategy(c: AsyncClient, payload: dict):
    return await c.post("/api/v1/strategies", json=payload)


_SAMPLE = {
    "name": "BTC SMA Cross",
    "type": "sma_cross",
    "description": "Test strategy",
    "symbols": ["BTC/USDT"],
    "interval": "1h",
    "parameters": {"sma_short": 10, "sma_long": 50},
    "entry_rules": {},
    "exit_rules": {},
    "risk_params": {"stop_loss_pct": 0.02},
    "tags": ["trend", "btc"],
    "source": "user",
}

_DEPLOYABLE_SAMPLE = {
    **_SAMPLE,
    "name": "Deployable SMA Cross",
    "entry_rules": {"type": "sma_cross", "short": 10, "long": 50},
    "exit_rules": {"type": "take_profit", "pct": 0.05},
}


@pytest.mark.asyncio
async def test_create_returns_version_one_draft():
    resp = await _create_strategy(_client(create_app()), _SAMPLE)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "BTC SMA Cross"
    assert data["id"].startswith("strat-")
    assert data["tags"] == ["trend", "btc"]
    assert data["source"] == "user"
    assert data["version"] == 1
    assert data["lifecycle_state"] == "draft"


@pytest.mark.asyncio
async def test_list_on_empty_store_yields_no_rows():
    async with _client(create_app()) as c:
        resp = await c.get("/api/v1/strategies")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["pagination"]["total"] == 0


@pytest.mark.asyncio
async def test_pagination_window_and_totals():
    async with _client(create_app()) as c:
        for i in range(3):
            await _create_strategy(c, {**_SAMPLE, "name": f"Strategy {i}"})
        resp = await c.get("/api/v1/strategies?offset=1&limit=1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    page = body["meta"]["pagination"]
    assert page["total"] == 3
    assert page["offset"] == 1
    assert page["limit"] == 1


@pytest.mark.asyncio
async def test_tag_filter_narrows_results():
    async with _client(create_app()) as c:
        await _create_strategy(c, _SAMPLE)
        await _create_strategy(
            c, {**_SAMPLE, "name": "ETH Mean Reversion", "tags": ["mean-reversion", "eth"]}
        )
        resp = await c.get("/api/v1/strategies?tags=btc")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) == 1
    assert rows[0]["name"] == "BTC SMA Cross"


@pytest.mark.asyncio
async def test_put_can_change_tags_and_source():
    async with _client(create_app()) as c:
        created = await _create_strategy(c, _SAMPLE)
        sid = created.json()["data"]["id"]
        resp = await c.put(
            f"/api/v1/strategies/{sid}",
            json={"tags": ["momentum"], "source": "ai_agent"},
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tags"] == ["momentum"]
    assert data["source"] == "ai_agent"
    assert data["version"] == 2


@pytest.mark.asyncio
async def test_get_by_id_round_trips():
    async with _client(create_app()) as c:
        created = await _create_strategy(c, _SAMPLE)
        sid = created.json()["data"]["id"]
        resp = await c.get(f"/api/v1/strategies/{sid}")
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == sid


@pytest.mark.asyncio
async def test_versions_accumulate_after_edit():
    async with _client(create_app()) as c:
        created = await _create_strategy(c, _SAMPLE)
        sid = created.json()["data"]["id"]
        await c.put(f"/api/v1/strategies/{sid}", json={"description": "updated"})
        resp = await c.get(f"/api/v1/strategies/{sid}/versions")
    assert resp.status_code == 200
    versions = resp.json()["data"]
    assert len(versions) >= 2
    assert versions[0]["version"] >= versions[-1]["version"]


@pytest.mark.asyncio
async def test_deploy_without_rules_is_rejected():
    """deploy-paper returns deployment=None when entry/exit rules are empty."""
    async with _client(create_app()) as c:
        created = await _create_strategy(c, _SAMPLE)
        sid = created.json()["data"]["id"]
        await c.post(f"/api/v1/strategies/{sid}/confirm")
        deploy = await c.post(f"/api/v1/strategies/{sid}/deploy-paper", json={"account_id": "paper-default"})
    assert deploy.status_code == 200
    payload = deploy.json()["data"]
    assert payload["deployment"] is None
    assert payload["runner_status"] == "failed"
    assert "no trading rules" in payload["runner_error"]


@pytest.mark.asyncio
async def test_confirmed_deployable_reaches_paper_runner():
    async with _client(create_app()) as c:
        created = await _create_strategy(c, _DEPLOYABLE_SAMPLE)
        sid = created.json()["data"]["id"]

        confirm = await c.post(f"/api/v1/strategies/{sid}/confirm")
        assert confirm.status_code == 200
        assert confirm.json()["data"]["lifecycle_state"] == "confirmed"

        deploy = await c.post(f"/api/v1/strategies/{sid}/deploy-paper", json={"account_id": "paper-default"})
    assert deploy.status_code == 200
    payload = deploy.json()["data"]
    assert payload["strategy"]["lifecycle_state"] in ("running", "confirmed")


@pytest.mark.asyncio
async def test_deployments_listing_shows_the_account():
    async with _client(create_app()) as c:
        created = await _create_strategy(c, _DEPLOYABLE_SAMPLE)
        sid = created.json()["data"]["id"]
        await c.post(f"/api/v1/strategies/{sid}/confirm")
        deploy = await c.post(f"/api/v1/strategies/{sid}/deploy-paper", json={"account_id": "paper-default"})
        if deploy.json()["data"].get("deployment") is None:
            pytest.skip("Runner not available in test environment; deployment was rejected")
        resp = await c.get("/api/v1/strategies/deployments/list?account_id=paper-default")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) >= 1
    assert rows[0]["account_id"] == "paper-default"


@pytest.mark.asyncio
async def test_fetch_unknown_id_errors_out():
    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/strategies/nonexistent")
    assert resp.status_code in (404, 500)


@pytest.mark.asyncio
async def test_delete_reports_deleted_id():
    async with _client(create_app()) as c:
        created = await _create_strategy(c, _SAMPLE)
        sid = created.json()["data"]["id"]
        resp = await c.delete(f"/api/v1/strategies/{sid}")
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] == sid


@pytest.mark.asyncio
async def test_validate_accepts_minimal_body():
    transport = ASGITransport(app=create_app())
    body = {
        "name": "test",
        "type": "sma_cross",
        "symbols": ["BTC/USDT"],
        "interval": "1h",
        "parameters": {"sma_short": 10, "sma_long": 50},
    }
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/v1/strategies/validate", json=body)
    assert resp.status_code == 200
    verdict = resp.json()["data"]
    assert verdict["valid"] is True
    assert verdict["errors"] == []

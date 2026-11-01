"""Paper trading endpoints wired to real account / order / position managers."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from app.core.dependencies import (
    get_paper_account_manager,
    get_paper_order_manager,
    get_paper_position_manager,
)
from app.main import create_app
from httpx import ASGITransport, AsyncClient

# Real selene_space_paper managers keep this an integration-grade check.
from selene_space_paper.accounts import AccountManager
from selene_space_paper.orders import PaperOrderManager
from selene_space_paper.positions import PositionManager


@asynccontextmanager
async def paper_api():
    """Fresh app + real managers; yields a bound client.

    The manager instances are created once and captured by the override
    lambdas — building them inside the lambda would hand every request a
    different empty store.
    """
    app = create_app()
    accounts = AccountManager()
    orders = PaperOrderManager()
    positions = PositionManager()
    app.dependency_overrides[get_paper_account_manager] = lambda: accounts
    app.dependency_overrides[get_paper_order_manager] = lambda: orders
    app.dependency_overrides[get_paper_position_manager] = lambda: positions
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as c:
        yield c


def _account_body(name: str, balance: int) -> dict:
    return {"initial_balance": balance, "name": name}


def _order_body(account_id: str, **overrides) -> dict:
    base = {
        "quantity": 0.1,
        "order_type": "market",
        "side": "buy",
        "symbol": "BTC/USDT",
        "account_id": account_id,
    }
    return {**base, **overrides}


async def _open_account(c: AsyncClient, name: str = "A1") -> str:
    made = await c.post("/api/v1/paper/accounts", json=_account_body(name, 1000))
    return made.json()["data"]["id"]


@pytest.mark.asyncio
async def test_account_creation_persists_name_and_balance():
    async with paper_api() as c:
        resp = await c.post("/api/v1/paper/accounts", json=_account_body("Test Account", 50000))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["initial_balance"] == 50000
        assert data["name"] == "Test Account"


@pytest.mark.asyncio
async def test_listing_reflects_created_accounts():
    async with paper_api() as c:
        await _open_account(c)
        listed = await c.get("/api/v1/paper/accounts")
        assert listed.status_code == 200
        assert len(listed.json()["data"]) == 1


@pytest.mark.asyncio
async def test_fetch_account_by_id():
    async with paper_api() as c:
        aid = await _open_account(c)
        got = await c.get(f"/api/v1/paper/accounts/{aid}")
        assert got.status_code == 200
        assert got.json()["data"]["id"] == aid


@pytest.mark.asyncio
async def test_unknown_account_errors_out():
    async with paper_api() as c:
        missing = await c.get("/api/v1/paper/accounts/nonexistent")
        assert missing.status_code in (404, 500)


@pytest.mark.asyncio
async def test_market_order_is_accepted():
    async with paper_api() as c:
        aid = await _open_account(c)
        placed = await c.post("/api/v1/paper/orders", json=_order_body(aid))
        assert placed.status_code == 200
        data = placed.json()["data"]
        assert data["status"] == "accepted"
        assert data["symbol"] == "BTC/USDT"
        assert data["side"] == "buy"


@pytest.mark.asyncio
async def test_order_history_lists_submitted_orders():
    async with paper_api() as c:
        aid = await _open_account(c)
        await c.post("/api/v1/paper/orders", json=_order_body(aid))
        history = await c.get(f"/api/v1/paper/orders?account_id={aid}")
        assert history.status_code == 200
        assert len(history.json()["data"]) == 1


@pytest.mark.asyncio
async def test_fresh_account_has_no_positions():
    async with paper_api() as c:
        aid = await _open_account(c)
        positions = await c.get(f"/api/v1/paper/positions?account_id={aid}")
        assert positions.status_code == 200
        assert positions.json()["data"] == []


@pytest.mark.asyncio
async def test_fresh_account_has_no_pnl_rows():
    async with paper_api() as c:
        aid = await _open_account(c)
        pnl = await c.get(f"/api/v1/paper/pnl?account_id={aid}")
        assert pnl.status_code == 200
        assert pnl.json()["data"] == []

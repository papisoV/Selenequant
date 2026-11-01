"""v0.1 closed-loop integration: strategy -> backtest -> paper -> risk -> live gate.

Real endpoint wiring against the engine packages; no exchange connection needed.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import app.api.v1.backtests as bt_mod
import app.api.v1.strategies as strat_mod
import pytest
from app.core.dependencies import (
    get_execution_engine,
    get_live_engine,
    get_paper_account_manager,
    get_paper_order_manager,
    get_paper_position_manager,
)
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_paper.accounts import AccountManager
from selene_space_paper.orders import PaperOrderManager
from selene_space_paper.paper_execution import PaperExecutionEngine
from selene_space_paper.positions import PositionManager
from selene_space_risk.engine import RiskEngine


@pytest.fixture(autouse=True)
def _wipe_stores():
    bt_mod._tasks.clear()
    strat_mod._strategies.clear()
    yield
    bt_mod._tasks.clear()
    strat_mod._strategies.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sma_node(period: int) -> dict:
    return {"indicator": "sma", "params": {"period": period}}


def _cross_rule(operator: str) -> dict:
    return {**_sma_node(10), "operator": operator, "comparator": _sma_node(50)}


def _strategy_payload() -> dict:
    return {
        "name": "BTC SMA Cross",
        "type": "sma_cross",
        "symbols": ["BTC/USDT"],
        "interval": "1h",
        "parameters": {"sma_short": 10, "sma_long": 50},
        "entry_rules": {"long": [_cross_rule("crosses_above")]},
        "exit_rules": {"close_long": [_cross_rule("crosses_below")]},
        "risk_params": {"stop_loss_pct": 0.02},
    }


STRAT = _strategy_payload()


@asynccontextmanager
async def loop_api(swallow: bool = False):
    """App with real paper managers injected (lifespan not run).

    Managers are instantiated once and captured by the overrides — building
    them inside the lambda would give each request a fresh empty store.
    """
    app = create_app()
    accounts = AccountManager()
    orders = PaperOrderManager()
    positions = PositionManager()
    app.dependency_overrides[get_paper_account_manager] = lambda: accounts
    app.dependency_overrides[get_paper_order_manager] = lambda: orders
    app.dependency_overrides[get_paper_position_manager] = lambda: positions
    opts = {} if not swallow else {"raise_app_exceptions": False}
    async with AsyncClient(transport=ASGITransport(app=app, **opts), base_url="http://test") as c:
        yield c


async def _open_backtest(c: AsyncClient, capital: int | None = None):
    """Create STRAT, queue a backtest, return (task_id, strategy_id)."""
    created = await c.post("/api/v1/strategies", json=STRAT)
    assert created.status_code == 200
    sid = created.json()["data"]["id"]

    spec = {"strategy_id": sid}
    if capital is not None:
        spec["initial_capital"] = capital
    started = await c.post("/api/v1/backtests", json=spec)
    assert started.status_code == 202
    return started.json()["data"]["task_id"], sid


async def _poll_terminal(c: AsyncClient, task_id: str):
    snapshot = None
    for _ in range(30):
        await asyncio.sleep(0.5)
        snapshot = (await c.get(f"/api/v1/backtests/{task_id}")).json()["data"]
        if snapshot["status"] in ("completed", "failed"):
            break
    return snapshot


# --- backtest loop ----------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_result_carries_flattened_metrics():
    """POST /backtests -> GET /{task_id}: response includes strategy_name and flattened metrics."""
    async with loop_api(swallow=True) as c:
        task_id, sid = await _open_backtest(c, capital=10000)
        snapshot = await _poll_terminal(c, task_id)

    assert snapshot is not None
    assert "id" in snapshot
    assert snapshot["strategy_id"] == sid
    assert snapshot["strategy_name"] == "BTC SMA Cross"
    assert snapshot["status"] in ("completed", "failed")


@pytest.mark.asyncio
async def test_backtest_listing_exposes_frontend_fields():
    """GET /backtests: each item has `id` and `strategy_name` for frontend display."""
    async with loop_api(swallow=True) as c:
        await _open_backtest(c)
        await asyncio.sleep(2.0)
        rows = (await c.get("/api/v1/backtests")).json()["data"]

    assert len(rows) >= 1
    head = rows[0]
    assert "id" in head
    assert "strategy_id" in head


# --- paper account state ----------------------------------------------------


@pytest.mark.asyncio
async def test_account_response_has_both_balance_fields():
    """POST /paper/accounts: response has initial_balance and current_balance."""
    async with loop_api() as c:
        made = await c.post(
            "/api/v1/paper/accounts",
            json={"initial_balance": 50000, "name": "Test"},
        )
    info = made.json()["data"]
    assert made.status_code == 200
    assert info["initial_balance"] == 50000
    assert "current_balance" in info


@pytest.mark.asyncio
async def test_pnl_endpoint_returns_rows_for_fresh_account():
    """GET /paper/pnl: returns PnL data (empty for no positions)."""
    async with loop_api() as c:
        opened = await c.post(
            "/api/v1/paper/accounts", json={"name": "Test", "initial_balance": 10000}
        )
        aid = opened.json()["data"]["id"]
        report = await c.get(f"/api/v1/paper/pnl?account_id={aid}")
    assert report.status_code == 200
    assert report.json()["data"] == []


# --- strategy persistence ---------------------------------------------------


@pytest.mark.asyncio
async def test_strategy_row_exposes_symbol_and_symbols():
    """GET /strategies: response includes both `symbols` list and `symbol` string."""
    async with loop_api() as c:
        await c.post("/api/v1/strategies", json=STRAT)
        listed = await c.get("/api/v1/strategies")
    rows = listed.json()["data"]
    assert listed.status_code == 200
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTC/USDT"
    assert rows[0]["symbols"] == ["BTC/USDT"]


# --- risk pre-check ---------------------------------------------------------


def _risk_wired(rules):
    """App + running paper engine wired with a RiskEngine over `rules`."""
    engine = PaperExecutionEngine(initial_balance=100000)
    app = create_app()
    app.dependency_overrides[get_execution_engine] = lambda: engine
    app.dependency_overrides[get_live_engine] = lambda: engine
    return app, engine, RiskEngine(rules=rules)


@pytest.mark.asyncio
async def test_blacklisted_symbol_is_blocked_by_risk_engine():
    """POST /trading/orders: risk engine blocks order when conditions are met."""
    from app.core.dependencies import set_risk_engine as _set_risk

    from selene_space_risk.rules import SymbolBlacklistRule

    app, engine, risk = _risk_wired([SymbolBlacklistRule(blacklist=["BLOCKED/USDT"])])
    _set_risk(risk)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await engine.start()
            rejected = await c.post(
                "/api/v1/trading/orders",
                json={
                    "quantity": 1.0,
                    "symbol": "BLOCKED/USDT",
                    "side": "buy",
                    "order_type": "market",
                },
            )

        assert rejected.status_code == 403
        detail = rejected.json()["detail"]
        assert "blacklisted" in detail.lower() or "黑名单" in detail
    finally:
        _set_risk(None)


@pytest.mark.asyncio
async def test_ordinary_limit_order_passes_risk_checks():
    """POST /trading/orders: normal order passes risk checks."""
    from app.core.dependencies import set_risk_engine as _set_risk

    from selene_space_risk.rules import create_default_rules

    app, engine, risk = _risk_wired(create_default_rules())
    _set_risk(risk)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await engine.start()
            accepted = await c.post(
                "/api/v1/trading/orders",
                json={
                    "price": 50000.0,
                    "quantity": 0.001,
                    "side": "buy",
                    "symbol": "BTC/USDT",
                    "order_type": "limit",
                },
            )

        assert accepted.status_code == 200
    finally:
        _set_risk(None)


# --- v0.1 live-mode gate ----------------------------------------------------


@pytest.mark.asyncio
async def test_switching_to_live_mode_is_refused():
    """PUT /trading/mode to 'live' is rejected in v0.1."""
    async with loop_api(swallow=True) as c:
        turned = await c.put("/api/v1/trading/mode", json={"mode": "live"})
    assert turned.status_code == 400
    why = turned.json()["detail"].lower()
    assert "not available" in why or "not configured" in why


# --- strategy -> backtest field contract -------------------------------------


@pytest.mark.asyncio
async def test_create_then_backtest_field_names_align():
    """Full flow: create strategy -> start backtest -> verify field names align."""
    async with loop_api(swallow=True) as c:
        drafted = (await c.post("/api/v1/strategies", json=STRAT)).json()["data"]

        assert "id" in drafted
        assert "name" in drafted
        assert "symbol" in drafted
        assert "symbols" in drafted

        queued = await c.post(
            "/api/v1/backtests",
            json={
                "commission_rate": 0.001,
                "strategy_id": drafted["id"],
                "initial_capital": 20000,
            },
        )
        receipt = queued.json()["data"]

    assert queued.status_code == 202
    assert "task_id" in receipt
    assert "status" in receipt

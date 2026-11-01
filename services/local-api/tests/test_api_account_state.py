"""API tests for AccountState REST endpoints (protocol §4 Step 3, §5).

Covers GET /paper/accounts/{account_id}/state:
- engine None -> empty-state document (no 5xx), mode=paper, zero watermark.
- normal engine -> populated document with paper equity.
- account ownership 404 for a missing account.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.core.dependencies import (
    get_execution_engine,
    get_paper_account_manager,
    get_paper_position_manager,
)
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from selene_space_paper.accounts import AccountManager
from selene_space_paper.positions import PositionManager
from selene_space_types.trading import BalanceUpdate, ExecutionMode

pytestmark = pytest.mark.asyncio


class _FakeAccount:
    def __init__(self, aid: str) -> None:
        self.id = aid
        self.initial_balance = Decimal("10000")
        self.total_realized_pnl = Decimal("100")
        self.total_fee = Decimal("10")


class _FakeAccountMgr:
    def __init__(self, account: _FakeAccount) -> None:
        self._account = account

    def get_account(self, aid: str) -> _FakeAccount | None:
        return self._account if aid == self._account.id else None


class _FakePosMgr:
    def get_open_positions(self, aid: str) -> list:
        return []


class _FakeEngine:
    mode = ExecutionMode.PAPER
    exchange = "paper"

    def __init__(self, account: _FakeAccount) -> None:
        self._account_mgr = _FakeAccountMgr(account)
        self._position_mgr = _FakePosMgr()

    async def get_balances(self, account_id: str) -> list[BalanceUpdate]:
        return [
            BalanceUpdate(
                exchange="paper",
                asset="USDT",
                free=Decimal("10090"),
                locked=Decimal("0"),
                timestamp=1711000000000,
            )
        ]

    async def get_positions(self, account_id: str) -> list:
        return []

    async def get_orders(self, account_id: str) -> list:
        return []

    async def get_fills(self, account_id: str) -> list:
        return []


def _make_app(accounts: AccountManager, positions: PositionManager, engine=None):
    app = create_app()
    app.dependency_overrides[get_paper_account_manager] = lambda: accounts
    app.dependency_overrides[get_paper_position_manager] = lambda: positions
    if engine is not None:
        app.dependency_overrides[get_execution_engine] = lambda: engine
    return app


@pytest.mark.asyncio
async def test_paper_account_state_empty_engine() -> None:
    accounts = AccountManager()
    positions = PositionManager()
    app = _make_app(accounts, positions, engine=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        acct = accounts.create_account(name="A1", initial_balance=1000)
        aid = acct.id
        resp = await c.get(f"/api/v1/paper/accounts/{aid}/state")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["account_id"] == aid
    assert body["mode"] == "paper"
    assert body["balances"] == []
    assert body["positions"] == []
    assert body["orders"] == []
    assert body["trades"] == []
    assert body["equity"] is None
    assert body["seq"] == {"orders": 0, "positions": 0, "balances": 0}


@pytest.mark.asyncio
async def test_paper_account_state_normal_engine() -> None:
    accounts = AccountManager()
    positions = PositionManager()
    acct = accounts.create_account(name="A1", initial_balance=1000)
    engine = _FakeEngine(_FakeAccount(acct.id))
    app = _make_app(accounts, positions, engine=engine)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(f"/api/v1/paper/accounts/{acct.id}/state")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["mode"] == "paper"
    assert body["exchange"] == "paper"
    assert len(body["balances"]) == 1
    assert body["balances"][0]["free"] == 10090.0
    # F11 equity = 10000 + 100 - 10 + 0.
    assert body["equity"] == 10090.0
    assert body["positions"] == []
    assert body["orders"] == []
    assert body["trades"] == []


@pytest.mark.asyncio
async def test_paper_account_state_missing_account_404() -> None:
    accounts = AccountManager()
    positions = PositionManager()
    app = _make_app(accounts, positions, engine=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/paper/accounts/nonexistent/state")
    assert resp.status_code == 404


class _TradingFakeEngine:
    mode = ExecutionMode.PAPER
    exchange = "paper"

    async def get_balances(self, account_id: str) -> list[BalanceUpdate]:
        return [
            BalanceUpdate(
                exchange="paper",
                asset="USDT",
                free=Decimal("5000.0"),
                locked=Decimal("0.0"),
                timestamp=1711000000000,
            )
        ]

    async def get_positions(self, account_id: str) -> list:
        return []

    async def get_orders(self, account_id: str) -> list:
        return []

    async def get_fills(self, account_id: str) -> list:
        return []


class TestTradingAccountState:
    @pytest.mark.asyncio
    async def test_trading_account_state_empty_engine(self) -> None:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/trading/account-state")
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["account_id"] == "paper-default"
        assert body["mode"] == "paper"
        assert body["balances"] == []
        assert body["positions"] == []
        assert body["orders"] == []
        assert body["trades"] == []
        assert body["equity"] is None
        assert body["seq"] == {"orders": 0, "positions": 0, "balances": 0}

    @pytest.mark.asyncio
    async def test_trading_account_state_with_engine(self) -> None:
        app = create_app()
        app.dependency_overrides[get_execution_engine] = lambda: _TradingFakeEngine()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/trading/account-state")
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["mode"] == "paper"
        assert body["exchange"] == "paper"
        assert len(body["balances"]) == 1
        assert body["balances"][0]["free"] == 5000.0
        assert body["positions"] == []
        assert body["orders"] == []
        assert body["trades"] == []

"""Publish gate: deployment requires prior backtest evidence.

QD 语义移植: live 部署是硬门(该策略从未回测过 → 拒绝), paper 部署是软提示
(不拒绝,响应带 backtest_warning)。判据 = backtests 表有无该 strategy_id 的行
——失败的回测只进 backtest_tasks,不写 backtests,所以"有行"即"有成功回测"。
用 :memory: SQLite + set_db_manager 注入,避免触碰 data/klines.db。
"""

from __future__ import annotations

from datetime import UTC, datetime

import app.api.v1.strategies as strategies_mod
import pytest
import pytest_asyncio
from app.core.dependencies import set_db_manager
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.backtests import BacktestRepository
from selene_space_storage.sqlite import AsyncSQLiteManager
from selene_space_types.strategy import BacktestMetrics, BacktestResult


@pytest_asyncio.fixture
async def db_manager():
    runner = MigrationRunner(ALL_MIGRATIONS)
    db = AsyncSQLiteManager(":memory:", migration_runner=runner)
    await db.connect()
    set_db_manager(db)
    yield db
    set_db_manager(None)
    await db.close()


@pytest.fixture(autouse=True)
def _wipe_store():
    strategies_mod._strategies.clear()
    strategies_mod._strategy_versions.clear()
    yield
    strategies_mod._strategies.clear()
    strategies_mod._strategy_versions.clear()


_DEPLOYABLE = {
    "name": "Gate Test SMA",
    "type": "sma_cross",
    "description": "gate",
    "symbols": ["BTC/USDT"],
    "interval": "1h",
    "parameters": {"sma_short": 10, "sma_long": 50},
    "entry_rules": {"type": "sma_cross", "short": 10, "long": 50},
    "exit_rules": {"type": "take_profit", "pct": 0.05},
    "risk_params": {"stop_loss_pct": 0.02},
    "tags": [],
    "source": "user",
}


async def _create_confirmed(c: AsyncClient) -> str:
    created = await c.post("/api/v1/strategies", json=_DEPLOYABLE)
    assert created.status_code == 200, created.text
    sid: str = created.json()["data"]["id"]
    confirm = await c.post(f"/api/v1/strategies/{sid}/confirm")
    assert confirm.status_code == 200
    return sid


async def _seed_backtest(db, sid: str) -> None:
    result = BacktestResult(
        id=f"bt-{sid[:8]}",
        strategy_id=sid,
        strategy_version=1,
        symbol="BTC/USDT",
        interval="1h",
        start_date=datetime(2024, 1, 1, tzinfo=UTC),
        end_date=datetime(2024, 2, 1, tzinfo=UTC),
        metrics=BacktestMetrics(
            total_return=0.1,
            annual_return=0.1,
            sharpe_ratio=1.0,
            max_drawdown=-0.05,
            win_rate=0.55,
            profit_factor=1.2,
            total_trades=3,
        ),
        equity_curve=[10000.0, 11000.0],
        drawdown_curve=[0.0, -0.02],
        buy_hold_curve=[10000.0, 10500.0],
        trades=[],
        trades_count=3,
        created_at=1711000000000,
    )
    await BacktestRepository(db).save(result)


@pytest.mark.asyncio
async def test_paper_deploy_without_backtest_carries_warning():
    """Paper 软门:不拒绝,但响应带 backtest_warning 非空。"""
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        sid = await _create_confirmed(c)
        resp = await c.post(
            f"/api/v1/strategies/{sid}/deploy-paper",
            json={"account_id": "paper-default"},
        )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body.get("backtest_warning"), "expected non-empty backtest_warning"


@pytest.mark.asyncio
async def test_paper_deploy_with_backtest_no_warning(db_manager):
    """有成功回测(种一行 backtests)→ backtest_warning 为空。"""
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        sid = await _create_confirmed(c)
        await _seed_backtest(db_manager, sid)
        resp = await c.post(
            f"/api/v1/strategies/{sid}/deploy-paper",
            json={"account_id": "paper-default"},
        )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert (
        body.get("backtest_warning") in (None, "")
    ), f"expected empty warning with backtest on file, got: {body.get('backtest_warning')}"


@pytest.mark.asyncio
async def test_live_deploy_without_backtest_is_blocked(db_manager, monkeypatch):
    """Live 硬门:从未回测的策略不许上实盘。

    community edition 的 get_live_engine() 返回 None 会先短路,所以
    monkeypatch 它返回哨兵对象,让请求能走到门的位置。
    """
    from app.core import dependencies as deps_mod

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        sid = await _create_confirmed(c)

        def _fake_engine():
            return object()  # truthy sentinel,足够越过 engine None 短路

        monkeypatch.setattr(deps_mod, "get_live_engine", _fake_engine)
        resp = await c.post(
            f"/api/v1/strategies/{sid}/deploy-live",
            json={"venue": "binance", "account_id": "live-x"},
        )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body.get("deployed") is False
    assert body.get("runner_status") == "blocked"
    assert "回测" in (
        body.get("runner_error") or ""
    ), f"expected gate rejection mentioning backtest, got: {body.get('runner_error')}"


@pytest.mark.asyncio
async def test_live_deploy_with_backtest_passes_gate(db_manager, monkeypatch):
    """有回测证据 → 门放行,继续走 runner 部署流程(此测试环境 runner 未初始化,
    预期 runner_error 含 'not initialized' 而非 'blocked')。"""
    from app.core import dependencies as deps_mod

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        sid = await _create_confirmed(c)
        await _seed_backtest(db_manager, sid)

        def _fake_engine():
            return object()

        monkeypatch.setattr(deps_mod, "get_live_engine", _fake_engine)
        resp = await c.post(
            f"/api/v1/strategies/{sid}/deploy-live",
            json={"venue": "binance", "account_id": "live-x"},
        )
    assert resp.status_code == 200
    body = resp.json()["data"]
    # 门放行后 runner 未初始化会失败,但绝不能是 blocked(门语义)
    assert body.get("runner_status") != "blocked"
    assert "回测" not in (body.get("runner_error") or "")

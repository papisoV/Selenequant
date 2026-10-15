"""US-102: /deployments/list rows carry runtime health from the runner.

无 runner(测试默认)→ runtime=null;前端据此区分"等 runner"和"死了"。
有 runner 时注入 fake runner 验证字段透传。
"""

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


@pytest.mark.asyncio
async def test_deployments_list_runtime_null_without_runner():
    """无 runner → 每行 runtime=null(不是缺字段,是显式 null)。"""
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        # 直接种一个内存 deployment,绕过 runner
        from selene_space_types.strategy import StrategyDeployment

        strategies_mod._strategy_deployments.append(
            StrategyDeployment(
                id="dep-rt1",
                strategy_id="strat-rt",
                strategy_version=1,
                account_id="paper-default",
                mode="paper",
                status="running",
                created_at=1711000000000,
            )
        )
        resp = await c.get("/api/v1/strategies/deployments/list")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert rows, "expected at least the seeded deployment"
    row = next(r for r in rows if r["id"] == "dep-rt1")
    assert "runtime" in row
    assert row["runtime"] is None


@pytest.mark.asyncio
async def test_deployments_list_runtime_from_runner(monkeypatch):
    """有 runner + slot → runtime 字段透传 bar_count/signals/errors。"""
    from app.core import dependencies as deps_mod

    class _FakeRunner:
        def get_slot_status(self, deployment_id: str):
            if deployment_id != "dep-rt2":
                return None
            return {
                "deployment_id": deployment_id,
                "strategy_id": "strat-rt",
                "account_id": "paper-default",
                "symbol": "BTC/USDT",
                "interval": "1h",
                "position": 0.0,
                "bar_count": 42,
                "signals_emitted": 3,
                "orders_placed": 1,
                "errors": 0,
                "last_signal_ts": 1711000012345,
                "recent_signals": [],
            }

    monkeypatch.setattr(deps_mod, "get_strategy_runner", lambda: _FakeRunner())

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        from selene_space_types.strategy import StrategyDeployment

        strategies_mod._strategy_deployments.append(
            StrategyDeployment(
                id="dep-rt2",
                strategy_id="strat-rt",
                strategy_version=1,
                account_id="paper-default",
                mode="paper",
                status="running",
                created_at=1711000000000,
            )
        )
        resp = await c.get("/api/v1/strategies/deployments/list")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    row = next(r for r in rows if r["id"] == "dep-rt2")
    rt = row["runtime"]
    assert rt is not None
    assert rt["bar_count"] == 42
    assert rt["signals_emitted"] == 3
    assert rt["errors"] == 0
    assert rt["last_signal_ts"] == 1711000012345

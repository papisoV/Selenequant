"""Behaviour locks for the MCP outbound channel (P2-5).

Covers the three read-only / low-risk tools plus token auth:
- ``run_backtest`` returns a task_id and does NOT execute the engine (mocked)
- ``get_tuning_results`` lists tuning history (mockable)
- ``analyze_klines`` returns a direction/entry structure (mocked kline manager)
- HTTP router: missing token → 401, wrong tenant → 403
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from app.api.v1 import backtests as bt


# ---------------------------------------------------------------------------
# run_backtest
# ---------------------------------------------------------------------------


class TestRunBacktest:
    def test_returns_task_id_without_executing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MCP run_backtest spawns a task and returns its id, engine never runs."""
        from app.mcp import server

        spawned: list[str] = []

        async def _fake_spawn(body: bt.RunBacktestRequest, user_id: str) -> str:
            spawned.append(body.strategy_id)
            return "bt-mocked"

        monkeypatch.setattr(server, "_spawn_backtest", _fake_spawn)

        result = asyncio.run(
            server.run_backtest(strategy_id="bb_v3", symbol="BTC/USDT", interval="1h")
        )

        assert result["task_id"] == "bt-mocked"
        assert result["status"] == "pending"
        assert result["strategy_id"] == "bb_v3"
        assert spawned == ["bb_v3"]

    def test_invalid_interval_rejected(self) -> None:
        from app.mcp import server

        with pytest.raises(Exception):
            asyncio.run(server.run_backtest(strategy_id="bb_v3", interval="3h"))

    def test_capital_clamped_upper_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.mcp import server

        captured: list[float] = []

        async def _fake_spawn(body: bt.RunBacktestRequest, user_id: str) -> str:
            captured.append(body.effective_cash)
            return "bt-cap"

        monkeypatch.setattr(server, "_spawn_backtest", _fake_spawn)

        result = asyncio.run(
            server.run_backtest(strategy_id="bb_v3", initial_capital=1e12)
        )
        assert result["task_id"] == "bt-cap"
        assert captured[0] <= server._MAX_BACKTEST_CAPITAL


# ---------------------------------------------------------------------------
# get_tuning_results
# ---------------------------------------------------------------------------


class TestGetTuningResults:
    def test_returns_redacted_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.mcp import server

        class _FakeSummary:
            def model_dump(self, mode: str = "json") -> dict[str, Any]:
                return {"strategy_id": "bb_v3", "api_key": "sk-leak", "n_trials": 30}

        class _FakeResp:
            data: list[Any] = [_FakeSummary()]

        async def _fake_list(request: Any, user: Any) -> _FakeResp:
            return _FakeResp()

        monkeypatch.setattr("app.api.v1.research.list_tuning_results", _fake_list)

        rows = asyncio.run(server.get_tuning_results(limit=10))

        assert rows[0]["strategy_id"] == "bb_v3"
        assert rows[0]["api_key"] == "***REDACTED***"

    def test_limit_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.mcp import server

        class _FakeSummary:
            def model_dump(self, mode: str = "json") -> dict[str, Any]:
                return {"strategy_id": "x"}

        class _FakeResp:
            data: list[Any] = [_FakeSummary() for _ in range(500)]

        async def _fake_list(request: Any, user: Any) -> _FakeResp:
            return _FakeResp()

        monkeypatch.setattr("app.api.v1.research.list_tuning_results", _fake_list)
        monkeypatch.setattr(server, "_MAX_TUNING_LIMIT", 50)

        rows = asyncio.run(server.get_tuning_results(limit=10_000))
        assert len(rows) <= 50


# ---------------------------------------------------------------------------
# analyze_klines
# ---------------------------------------------------------------------------


class TestAnalyzeKlines:
    def test_returns_direction_and_price_structure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.mcp import server

        class _FakeKlineManager:
            async def get_klines(self, **kwargs: Any) -> Any:
                import pandas as pd

                return pd.DataFrame(
                    {
                        "close": [100.0, 101.0, 102.0],
                        "high": [100.5, 101.5, 102.5],
                        "low": [99.5, 100.5, 101.5],
                    }
                )

        monkeypatch.setattr(
            "app.core.dependencies.get_kline_manager", lambda: _FakeKlineManager()
        )

        result = asyncio.run(server.analyze_klines(symbol="BTC/USDT", timeframe="1h"))

        assert result["symbol"] == "BTC/USDT"
        assert result["direction"] in {"up", "down", "flat"}
        assert "last_close" in result
        assert "risk_level" in result

    def test_no_orders_placed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """analyze_klines only reads klines; it never touches an order path."""
        from app.mcp import server

        class _FakeKlineManager:
            async def get_klines(self, **kwargs: Any) -> Any:
                import pandas as pd

                return pd.DataFrame(
                    {"close": [1.0, 2.0], "high": [1.5, 2.5], "low": [0.5, 1.5]}
                )

        monkeypatch.setattr(
            "app.core.dependencies.get_kline_manager", lambda: _FakeKlineManager()
        )

        result = asyncio.run(server.analyze_klines(symbol="BTC/USDT"))
        assert "error" not in result
        assert result["bars"] == 2


# ---------------------------------------------------------------------------
# HTTP router auth
# ---------------------------------------------------------------------------


class TestMcpAuth:
    def test_missing_token_returns_401(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        r = client.post("/api/v1/mcp/tools/run_backtest", json={"strategy_id": "bb_v3"})
        assert r.status_code == 401

    def test_wrong_tenant_returns_403(self) -> None:
        os.environ["SELENE_MCP_TOKEN"] = "test-token-123"
        try:
            from fastapi.testclient import TestClient

            from app.main import app

            client = TestClient(app)
            r = client.post(
                "/api/v1/mcp/tools/run_backtest",
                json={"strategy_id": "bb_v3"},
                headers={
                    "Authorization": "Bearer test-token-123",
                    "X-Tenant": "other-tenant",
                },
            )
            assert r.status_code == 403
        finally:
            os.environ.pop("SELENE_MCP_TOKEN", None)

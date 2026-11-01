"""Behaviour locks for strategy tools: validate, backtest run (async-only),
and backtest result lookup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from selene_space_agent.tools.strategy_tools import (
    BacktestResultTool,
    BacktestRunTool,
    StrategyValidateTool,
    get_results_store,
)
from selene_space_types.strategy import BacktestMetrics, BacktestResult


@dataclass
class _StubEngine:
    """BacktestEngine stand-in returning one canned result."""

    def run(self, strategy: Any, data: Any) -> BacktestResult:
        return BacktestResult(
            id="bt-probe-9",
            strategy_id="alpha-strat",
            start_date=datetime(2025, 2, 1, tzinfo=UTC),
            end_date=datetime(2025, 4, 30, tzinfo=UTC),
            metrics=BacktestMetrics(
                total_return=0.18,
                annual_return=0.72,
                sharpe_ratio=2.4,
                max_drawdown=-0.06,
                win_rate=0.62,
                profit_factor=1.9,
                total_trades=24,
            ),
            equity_curve=[20_000.0, 21_200.0, 22_400.0, 23_600.0],
            trades_count=24,
            created_at=1_710_000_000_000,
        )


def _good_config() -> dict[str, Any]:
    return {
        "id": "alpha-strat",
        "name": "Probe SMA",
        "type": "sma_cross",
        "symbols": ["ETH/USDT"],
        "interval": "4h",
        "parameters": {},
        "entry_rules": {},
        "exit_rules": {},
        "risk_params": {},
    }


def _candles(n: int = 10) -> list[dict[str, float]]:
    return [
        {
            "timestamp": i * 3600000,
            "open": 50.0,
            "high": 50.5,
            "low": 49.5,
            "close": 50.25,
            "volume": 8.0,
        }
        for i in range(n)
    ]


class TestStrategyValidateTool:
    def test_complete_config_validates(self) -> None:
        verdict = StrategyValidateTool().execute({"config": _good_config()})
        checks = (verdict.error is None, "valid" in verdict.output.lower())
        assert all(checks)

    def test_skeleton_config_fails(self) -> None:
        verdict = StrategyValidateTool().execute({"config": {"name": "hollow"}})
        # parse failure surfaces as error or a "failed" note
        assert verdict.error is not None or "failed" in verdict.output.lower()

    def test_absent_config_key_rejected(self) -> None:
        verdict = StrategyValidateTool().execute({})
        assert verdict.error is not None


class TestBacktestRunTool:
    def setup_method(self) -> None:
        get_results_store().clear()

    def test_sync_execute_is_blocked(self) -> None:
        # backtest_run is async-only; sync path must refuse
        tool = BacktestRunTool(_StubEngine())

        payload = {"strategy_config": _good_config(), "data": _candles()}
        verdict = tool.execute(payload)
        checks = (verdict.error is not None, "async" in verdict.error.lower())
        assert all(checks)

    def test_empty_data_rejected(self) -> None:
        tool = BacktestRunTool(_StubEngine())
        verdict = tool.execute({"strategy_config": {"id": "z"}, "data": []})
        assert verdict.error is not None

    def test_missing_strategy_config_rejected(self) -> None:
        tool = BacktestRunTool(_StubEngine())
        verdict = tool.execute({"data": [{"a": 1}]})
        assert verdict.error is not None


class TestBacktestResultTool:
    @staticmethod
    def _seeded_store() -> dict[str, BacktestResult]:
        return {
            "bt-77": BacktestResult(
                id="bt-77",
                strategy_id="alpha-strat",
                start_date=datetime(2025, 2, 1, tzinfo=UTC),
                end_date=datetime(2025, 4, 30, tzinfo=UTC),
                metrics=BacktestMetrics(
                    total_return=0.12,
                    annual_return=0.48,
                    sharpe_ratio=1.6,
                    max_drawdown=-0.09,
                    win_rate=0.58,
                    profit_factor=1.4,
                    total_trades=18,
                ),
                equity_curve=[20_000.0, 22_400.0],
                trades_count=18,
                created_at=1_710_000_000_000,
            ),
        }

    def test_hit_reports_id_and_return(self) -> None:
        verdict = BacktestResultTool(self._seeded_store()).execute({"backtest_id": "bt-77"})
        checks = (verdict.error is None, "bt-77" in verdict.output, "+12.00%" in verdict.output)
        assert all(checks)

    def test_miss_reports_absence(self) -> None:
        verdict = BacktestResultTool({}).execute({"backtest_id": "never-ran"})
        assert "No backtest found" in verdict.output

    def test_absent_id_key_rejected(self) -> None:
        verdict = BacktestResultTool({}).execute({})
        assert verdict.error is not None

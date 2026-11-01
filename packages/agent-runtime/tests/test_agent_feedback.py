"""Behaviour locks for the backtest feedback loop: issue detection,
suggestion generation, and suggestion application to strategy configs."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from selene_space_agent.feedback import (
    BacktestFeedbackLoop,
    StrategyImprovement,
    apply_suggestion,
)
from selene_space_agent.feedback.analyzer import (
    ISSUE_EXCESSIVE_DRAWDOWN,
    ISSUE_LOW_WIN_RATE,
    ISSUE_SHARPE_NEGATIVE,
    ISSUE_TOO_FEW_TRADES,
    ISSUE_TOO_MANY_TRADES,
)
from selene_space_strategy.models import EngineStrategyConfig
from selene_space_types.strategy import BacktestMetrics, BacktestResult, StrategyType


def _metrics(
    *,
    sharpe: float = 1.0,
    max_drawdown: float = -0.05,
    win_rate: float = 0.5,
    total_trades: int = 20,
) -> BacktestMetrics:
    return BacktestMetrics(
        total_return=0.1,
        annual_return=0.2,
        sharpe_ratio=sharpe,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        profit_factor=1.2,
        total_trades=total_trades,
    )


def _result(*, trades_count: int, metrics: BacktestMetrics) -> BacktestResult:
    return BacktestResult(
        id="bt-probe",
        strategy_id="probe-strat",
        start_date=datetime(2025, 5, 1, tzinfo=UTC),
        end_date=datetime(2025, 7, 1, tzinfo=UTC),
        metrics=metrics,
        equity_curve=[50_000.0, 50_500.0],
        trades_count=trades_count,
        created_at=1_735_000_000_000,
    )


# (issue constant, result kwargs that trigger it, expected suggestion category)
_DETECTION_CASES = [
    (ISSUE_SHARPE_NEGATIVE, dict(sharpe=-0.6), "parameter_tuning"),
    (ISSUE_EXCESSIVE_DRAWDOWN, dict(max_drawdown=-0.35), "risk"),
    (ISSUE_LOW_WIN_RATE, dict(win_rate=0.18), "add_filter"),
    (ISSUE_TOO_FEW_TRADES, dict(total_trades=4), "timeframe"),
    (ISSUE_TOO_MANY_TRADES, dict(total_trades=650), "add_filter"),
]


class TestIssueDetection:
    @pytest.mark.parametrize(("issue", "metric_kwargs", "category"), _DETECTION_CASES)
    def test_issue_flagged_with_matching_category(self, issue, metric_kwargs, category) -> None:
        trades = 650 if issue is ISSUE_TOO_MANY_TRADES else (4 if issue is ISSUE_TOO_FEW_TRADES else 20)
        report = BacktestFeedbackLoop().analyze(_result(trades_count=trades, metrics=_metrics(**metric_kwargs)))
        checks = (
            issue in report.issues,
            any(s.category == category for s in report.suggestions),
        )
        assert all(checks)

    def test_every_issue_yields_suggestions(self) -> None:
        loop = BacktestFeedbackLoop()
        for issue, metric_kwargs, _cat in _DETECTION_CASES:
            trades = 650 if issue is ISSUE_TOO_MANY_TRADES else (4 if issue is ISSUE_TOO_FEW_TRADES else 20)
            report = loop.analyze(_result(trades_count=trades, metrics=_metrics(**metric_kwargs)))
            checks = (issue in report.issues, bool(report.suggestions))
            assert all(checks), f"missing suggestions for {issue}"


def _base_config() -> EngineStrategyConfig:
    return EngineStrategyConfig(
        id="cfg-x",
        name="probe",
        type=StrategyType.SMA_CROSS,
        symbols=["ETH/USDT"],
        interval="1h",
        parameters={"sma_short": 10, "sma_long": 40},
    )


class TestApplySuggestion:
    def test_interval_merge(self) -> None:
        sug = StrategyImprovement(
            description="shift timeframe",
            category="timeframe",
            suggested_changes={"interval": "4h"},
        )
        assert apply_suggestion(_base_config(), sug).interval == "4h"

    def test_parameters_dict_merge_keeps_others(self) -> None:
        sug = StrategyImprovement(
            description="retune fast leg",
            category="parameter_tuning",
            suggested_changes={"parameters": {"sma_short": 14}},
        )
        updated = apply_suggestion(_base_config(), sug)
        pair = (updated.parameters["sma_short"], updated.parameters["sma_long"])
        assert pair == (14, 40)

    def test_risk_params_merge_preserves_existing(self) -> None:
        cfg = EngineStrategyConfig(
            id="cfg-y",
            name="guarded",
            type=StrategyType.SMA_CROSS,
            symbols=["ETH/USDT"],
            interval="1h",
            parameters={},
            risk_params={"max_position_pct": 0.15},
        )
        sug = StrategyImprovement(
            description="tighten stop",
            category="risk",
            suggested_changes={"risk_params": {"stop_loss_pct": 0.03}},
        )
        updated = apply_suggestion(cfg, sug)
        pair = (updated.risk_params["stop_loss_pct"], updated.risk_params["max_position_pct"])
        assert pair == (0.03, 0.15)

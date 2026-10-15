"""Report serializers: JSON emission and dict projection of BacktestResult."""

import json
from typing import Any

from selene_space_backtest.reports import to_dict, to_json
from selene_space_types.strategy import BacktestMetrics, BacktestResult

#: 断言里逐字引用的取值, 改这里必须同步改断言。
_SAMPLE_METRICS: dict[str, Any] = {
    "total_return": 0.15,
    "annual_return": 0.45,
    "sharpe_ratio": 1.8,
    "max_drawdown": -0.08,
    "win_rate": 0.55,
    "profit_factor": 1.6,
    "total_trades": 42,
}
_SAMPLE_EQUITY = [10000.0, 10500.0, 11500.0]
_SAMPLE_RESULT: dict[str, Any] = {
    "id": "bt-test-001",
    "strategy_id": "strat-001",
    "start_date": "2025-01-01T00:00:00",
    "end_date": "2025-03-31T23:59:59",
    "equity_curve": _SAMPLE_EQUITY,
    "trades_count": 42,
    "created_at": 1711000000000,
}


def _sample_result() -> BacktestResult:
    """构造一条固定的回测结果, 供各断言共享。"""
    return BacktestResult(metrics=BacktestMetrics(**_SAMPLE_METRICS), **_SAMPLE_RESULT)


class TestJsonEmission:
    def test_payload_round_trips_through_json(self) -> None:
        payload = json.loads(to_json(_sample_result()))
        assert payload["id"] == "bt-test-001"
        assert payload["metrics"]["total_return"] == 0.15
        assert payload["equity_curve"] == [10000.0, 10500.0, 11500.0]

    def test_dates_render_as_iso_text(self) -> None:
        payload = json.loads(to_json(_sample_result()))
        assert "2025-01-01" in payload["start_date"]
        assert "2025-03-31" in payload["end_date"]


class TestDictProjection:
    def test_projects_to_plain_dict(self) -> None:
        projected = to_dict(_sample_result())
        assert isinstance(projected, dict)
        assert projected["trades_count"] == 42
        assert "metrics" in projected

"""Wire-format locks for selene_space_types.strategy — StrategyType coverage,
StrategyConfig JSON compatibility, Signal, BacktestMetrics, BacktestResult."""

import json
from datetime import UTC, datetime

from selene_space_types.strategy import (
    BacktestMetrics,
    BacktestResult,
    Signal,
    StrategyConfig,
    StrategyType,
)
from selene_space_types.trading import OrderSide


def _recycle(model_cls, instance):
    return model_cls.model_validate_json(instance.model_dump_json())


def _sample_metrics() -> BacktestMetrics:
    return BacktestMetrics(
        total_return=0.22,
        annual_return=0.61,
        sharpe_ratio=2.05,
        max_drawdown=-0.11,
        win_rate=0.58,
        profit_factor=1.75,
        total_trades=63,
    )


def test_strategy_type_minimum_archetype_coverage():
    # Core archetypes must always be present. Additional values are added
    # as strategy templates expand (see packages/strategy-engine/...
    # /templates/), so assert minimum coverage rather than a fixed set.
    archetypes = {
        StrategyType.SMA_CROSS,
        StrategyType.RSI_REVERSAL,
        StrategyType.MACD,
        StrategyType.SENTIMENT,
        StrategyType.CUSTOM,
        StrategyType.ICT,
        StrategyType.ICT_FVG,
        StrategyType.ICT_OB,
        StrategyType.ICT_SWEEP,
    }
    everything = set(StrategyType)
    assert archetypes <= everything
    assert len(everything) >= 9


class TestStrategyConfigWire:
    def test_survives_json_cycle_with_rules(self):
        config = StrategyConfig(
            id="cfg-A2",
            name="ETH Momentum",
            type=StrategyType.SMA_CROSS,
            symbols=["BTC/USDT"],
            interval="1h",
            parameters={"sma_short": 12, "sma_long": 26},
            entry_rules={"condition": "sma_short > sma_long"},
            exit_rules={"condition": "sma_short < sma_long"},
            risk_params={"stop_loss_pct": 0.02},
        )
        assert _recycle(StrategyConfig, config) == config

    def test_plain_json_dict_is_yaml_compatible(self):
        """StrategyConfig must be JSON-serializable (YAML-compatible structure)."""
        config = StrategyConfig(
            id="cfg-B7",
            name="Handrolled",
            type=StrategyType.CUSTOM,
            symbols=["SOL/USDT"],
            interval="4h",
        )
        decoded = json.loads(config.model_dump_json())
        assert isinstance(decoded, dict)
        assert decoded["id"] == "cfg-B7"
        assert decoded["symbols"] == ["SOL/USDT"]


class TestSignalWire:
    def test_survives_json_cycle(self):
        sig = Signal(
            strategy_id="cfg-A2",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            strength=0.79,
            timestamp=1720004444555,
            reason="EMA 12 crossed above EMA 26",
        )
        assert _recycle(Signal, sig) == sig


class TestBacktestMetricsWire:
    def test_survives_json_cycle(self):
        metrics = _sample_metrics()
        assert _recycle(BacktestMetrics, metrics) == metrics


class TestBacktestResultWire:
    def test_survives_json_cycle_with_metrics(self):
        result = BacktestResult(
            id="run-Z3",
            strategy_id="cfg-A2",
            start_date=datetime(2024, 4, 1, tzinfo=UTC),
            end_date=datetime(2024, 6, 30, 23, 59, 59, tzinfo=UTC),
            metrics=_sample_metrics(),
            equity_curve=[20000.0, 20120.0, 20470.0],
            trades_count=63,
            created_at=1720004444666,
        )
        restored = _recycle(BacktestResult, result)
        assert restored == result
        assert restored.metrics.sharpe_ratio == 2.05

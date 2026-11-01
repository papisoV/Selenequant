"""YAML template coverage — flagship templates through load→compile→run,
plus a 33-file inventory sweep guaranteeing every shipped YAML boots."""

from __future__ import annotations

from pathlib import Path

import pytest

from selene_space_strategy.compiler import CompiledStrategy, compile
from selene_space_strategy.models import EngineStrategyConfig, load_strategy
from selene_space_strategy.runtime import StrategyRuntime
from selene_space_types.market import KlineEvent

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent / "selene_space_strategy" / "templates"
)


def _ramp_candles(count: int, start: float, step: float) -> list[KlineEvent]:
    """Deterministic linear price ramp of closed hourly candles."""
    return [
        KlineEvent(
            exchange="binance",
            symbol="BTC/USDT",
            timestamp=i * 3600000,
            interval="1h",
            open=start + i * step - 1,
            high=start + i * step + 1,
            low=start + i * step - 2,
            close=start + i * step,
            volume=1000.0,
            closed=True,
        )
        for i in range(count)
    ]


class TestSMACrossYaml:
    """sma_cross.yaml pipeline."""

    def test_identity_fields(self) -> None:
        config = load_strategy(TEMPLATES_DIR / "sma_cross.yaml")
        assert isinstance(config, EngineStrategyConfig)
        assert config.id == "template-sma-cross"
        assert config.type.value == "sma_cross"
        assert "BTC/USDT" in config.symbols

    def test_compile_shape(self) -> None:
        compiled = compile(load_strategy(TEMPLATES_DIR / "sma_cross.yaml"))
        assert isinstance(compiled, CompiledStrategy)
        assert len(compiled.long_entry_conditions) == 1
        assert len(compiled.close_long_conditions) == 1

    def test_sixty_bar_run(self) -> None:
        rt = StrategyRuntime(compile(load_strategy(TEMPLATES_DIR / "sma_cross.yaml")))
        for candle in _ramp_candles(60, start=100.0, step=1.0):
            rt.on_kline(candle)
        assert rt.bar_count == 60

    def test_yaml_risk_brackets(self) -> None:
        risk = load_strategy(TEMPLATES_DIR / "sma_cross.yaml").parsed_risk_params
        assert risk.stop_loss_pct == 0.03
        assert risk.take_profit_pct == 0.06


class TestRSIReversalYaml:
    """rsi_reversal.yaml pipeline."""

    def test_identity_fields(self) -> None:
        config = load_strategy(TEMPLATES_DIR / "rsi_reversal.yaml")
        assert isinstance(config, EngineStrategyConfig)
        assert config.id == "template-rsi-reversal"
        assert config.type.value == "rsi_reversal"

    def test_numeric_threshold_in_conditions(self) -> None:
        compiled = compile(load_strategy(TEMPLATES_DIR / "rsi_reversal.yaml"))
        assert isinstance(compiled, CompiledStrategy)
        assert len(compiled.long_entry_conditions) == 1
        assert len(compiled.close_long_conditions) == 1
        assert compiled.long_entry_conditions[0].comparator_value == 30.0

    def test_slide_triggers_oversold_buy(self) -> None:
        rt = StrategyRuntime(
            compile(load_strategy(TEMPLATES_DIR / "rsi_reversal.yaml"))
        )
        buys = []
        for candle in _ramp_candles(30, start=100.0, step=-2.0):
            sig = rt.on_kline(candle)
            if sig and sig.side.value == "buy":
                buys.append(sig)
        assert len(buys) >= 1

    def test_yaml_stop_loss(self) -> None:
        risk = load_strategy(TEMPLATES_DIR / "rsi_reversal.yaml").parsed_risk_params
        assert risk.stop_loss_pct == 0.02


class TestInventorySweep:
    """All shipped YAMLs must parse, compile and boot a runtime.

    Root vs subdirectory duplicates (adx_trend, atr_breakout, ...) are
    intentionally enumerated as distinct entries.
    """

    SHIPPED_TEMPLATES = [
        "adx_breakout.yaml",
        "adx_range.yaml",
        "adx_trend.yaml",
        "atr_breakout.yaml",
        "atr_trailing.yaml",
        "atr_volatility.yaml",
        "bollinger_breakout.yaml",
        "bollinger_reversal.yaml",
        "dca_grid.yaml",
        "ema_cross.yaml",
        "funding_rate_arb.yaml",
        "ict_fvg.yaml",
        "ict_killzone_discount.yaml",
        "ict_ob.yaml",
        "ict_premium_discount.yaml",
        "ict_sweep.yaml",
        "kdj_momentum.yaml",
        "kdj_trend.yaml",
        "ma_extended_runs.yaml",
        "macd_momentum.yaml",
        "rsi_momentum.yaml",
        "rsi_reversal.yaml",
        "sentiment.yaml",
        "sma_cross.yaml",
        "stochastic_momentum.yaml",
        "reversion/adx_range.yaml",
        "reversion/atr_reversal.yaml",
        "reversion/kdj_reversal.yaml",
        "reversion/stochastic_reversal.yaml",
        "trend/adx_stoch_trend.yaml",
        "trend/adx_trend.yaml",
        "trend/atr_breakout.yaml",
        "trend/atr_trailing.yaml",
        "trend/kdj_trend.yaml",
        "trend/stochastic_trend.yaml",
    ]

    @pytest.mark.parametrize("template_name", SHIPPED_TEMPLATES)
    def test_every_template_boots(self, template_name: str) -> None:
        compiled = compile(load_strategy(TEMPLATES_DIR / template_name))
        rt = StrategyRuntime(compiled)
        assert rt.position == "flat"
        assert rt.bar_count == 0

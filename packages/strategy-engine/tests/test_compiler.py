"""Compiler coverage — condition assembly, indicator cache construction,
deduplication across sides, numeric comparators, MACD column families,
and both compile-time failure branches."""

from __future__ import annotations

import pytest

from selene_space_strategy.compiler import (
    CompilationError,
    CompiledStrategy,
    compile,
)
from selene_space_strategy.models import (
    ConditionRule,
    EngineStrategyConfig,
    EntryRules,
    ExitRules,
    RiskParams,
)


def _config(
    config_id: str,
    name: str,
    strategy_type: str,
    entry_rules: EntryRules | None = None,
    exit_rules: ExitRules | None = None,
) -> EngineStrategyConfig:
    return EngineStrategyConfig(
        id=config_id,
        name=name,
        type=strategy_type,
        symbols=["BTC/USDT"],
        interval="1h",
        parsed_entry_rules=entry_rules or EntryRules(),
        parsed_exit_rules=exit_rules or ExitRules(),
    )


def _cross_rule(indicator: str, params: dict, operator: str, comparator) -> ConditionRule:
    return ConditionRule(
        indicator=indicator, params=params, operator=operator, comparator=comparator
    )


def _sma_cross_config() -> EngineStrategyConfig:
    """Long-side SMA(20)/SMA(50) crossover with mirrored exit."""
    fast, slow = {"period": 20}, {"period": 50}
    comparator = {"indicator": "sma", "params": slow}
    return EngineStrategyConfig(
        id="sma-cross",
        name="SMA Cross",
        type="sma_cross",
        symbols=["BTC/USDT"],
        interval="1h",
        parsed_entry_rules=EntryRules(
            long=[_cross_rule("sma", fast, "crosses_above", comparator)]
        ),
        parsed_exit_rules=ExitRules(
            close_long=[_cross_rule("sma", fast, "crosses_below", comparator)]
        ),
        parsed_risk_params=RiskParams(stop_loss_pct=0.02),
    )


class TestCompiledShape:
    """Structural output of compiling the canonical crossover."""

    def test_condition_counts_per_side(self) -> None:
        config = _sma_cross_config()
        compiled = compile(config)
        assert isinstance(compiled, CompiledStrategy)
        assert compiled.config is config
        assert len(compiled.long_entry_conditions) == 1
        assert len(compiled.close_long_conditions) == 1
        assert not compiled.short_entry_conditions
        assert not compiled.close_short_conditions

    def test_indicator_cache_holds_both_smas(self) -> None:
        compiled = compile(_sma_cross_config())
        assert len(compiled.indicators) == 2
        assert {ind.name for ind in compiled.indicators.values()} == {"sma"}

    def test_entry_condition_references_both_periods(self) -> None:
        cond = compile(_sma_cross_config()).long_entry_conditions[0]
        assert cond.indicator.name == "sma"
        assert cond.indicator.period == 20
        assert cond.operator == "crosses_above"
        assert cond.comparator_indicator is not None
        assert cond.comparator_indicator.name == "sma"
        assert cond.comparator_indicator.period == 50


class TestNumericComparators:
    """Scalar threshold path (RSI style)."""

    def test_rsi_threshold_lands_on_comparator_value(self) -> None:
        enter = _cross_rule("rsi", {"period": 14}, "less_than", 30.0)
        leave = _cross_rule("rsi", {"period": 14}, "greater_than", 70.0)
        compiled = compile(
            _config(
                "rsi-test",
                "RSI Test",
                "rsi_reversal",
                EntryRules(long=[enter]),
                ExitRules(close_long=[leave]),
            )
        )
        cond = compiled.long_entry_conditions[0]
        assert cond.comparator_value == 30.0
        assert cond.comparator_indicator is None


class TestDegenerateAndBrokenInputs:
    """Empty strategy compiles; unknown/broken params raise."""

    def test_no_rules_yields_empty_compilation(self) -> None:
        compiled = compile(_config("empty", "Empty Strategy", "custom"))
        assert not compiled.indicators
        assert not compiled.long_entry_conditions

    def test_unknown_indicator_names_the_culprit(self) -> None:
        ghost = {"period": 9}
        enter = _cross_rule("ichimoku", ghost, "crosses_above", 50.0)
        leave = _cross_rule("ichimoku", ghost, "crosses_below", 50.0)
        with pytest.raises(CompilationError, match="ichimoku"):
            compile(
                _config(
                    "bad",
                    "Bad Strategy",
                    "custom",
                    EntryRules(long=[enter]),
                    ExitRules(close_long=[leave]),
                )
            )

    def test_negative_period_is_rejected_at_compile(self) -> None:
        bad = {"period": -5}
        enter = _cross_rule("sma", bad, "crosses_above", 50.0)
        leave = _cross_rule("sma", bad, "crosses_below", 50.0)
        with pytest.raises(CompilationError):
            compile(
                _config(
                    "bad",
                    "Bad Params",
                    "custom",
                    EntryRules(long=[enter]),
                    ExitRules(close_long=[leave]),
                )
            )


class TestCacheDeduplication:
    """Same indicator+params on entry and exit share one instance."""

    def test_two_distinct_smas_only(self) -> None:
        fast, slow = {"period": 20}, {"period": 50}
        comparator = {"indicator": "sma", "params": slow}
        compiled = compile(
            _config(
                "shared",
                "Shared Indicators",
                "sma_cross",
                EntryRules(long=[_cross_rule("sma", fast, "crosses_above", comparator)]),
                ExitRules(close_long=[_cross_rule("sma", fast, "crosses_below", comparator)]),
            )
        )
        assert len(compiled.indicators) == 2


class TestMacdColumns:
    """MACD rules register base/signal/histogram column families."""

    MACD_PARAMS = {"fast_period": 12, "slow_period": 26, "signal_period": 9}

    def _macd_config(self) -> EngineStrategyConfig:
        signal_ref = {"indicator": "macd_signal", "params": self.MACD_PARAMS}
        return _config(
            "macd-test",
            "MACD Test",
            "macd",
            EntryRules(
                long=[
                    _cross_rule("macd", self.MACD_PARAMS, "crosses_above", signal_ref)
                ]
            ),
            ExitRules(
                close_long=[
                    _cross_rule("macd", self.MACD_PARAMS, "crosses_below", signal_ref)
                ]
            ),
        )

    def test_three_column_families_present(self) -> None:
        col_names = set(compile(self._macd_config()).indicators.keys())
        has_signal = any(c.startswith("macd_signal") for c in col_names)
        has_histogram = any(c.startswith("macd_histogram") for c in col_names)
        has_base = any(
            c.startswith("macd_")
            and not c.startswith("macd_signal")
            and not c.startswith("macd_histogram")
            for c in col_names
        )
        assert has_signal, f"macd_signal missing from {col_names}"
        assert has_histogram, f"macd_histogram missing from {col_names}"
        assert has_base, f"base macd_ column missing from {col_names}"

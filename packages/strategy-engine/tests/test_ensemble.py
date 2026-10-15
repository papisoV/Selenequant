"""StrategyEnsemble coverage — config surface, fusion enum, member and
weight registration, runtime lookup, kline fan-out, reset, signal payload."""

from __future__ import annotations

import pytest

from selene_space_strategy.compiler import compile
from selene_space_strategy.fusion.ensemble import (
    EnsembleConfig,
    EnsembleSignal,
    FusionMethod,
    StrategyEnsemble,
)
from selene_space_strategy.models import EngineStrategyConfig
from selene_space_types.market import KlineEvent


def _shell_config(strategy_id: str) -> EngineStrategyConfig:
    """Rule-free strategy shell for driving ensemble plumbing."""
    return EngineStrategyConfig(
        id=strategy_id,
        name=f"Strategy {strategy_id}",
        type="custom",
        symbols=["BTC/USDT"],
        interval="1h",
        parsed_entry_rules={"long": [], "short": []},
        parsed_exit_rules={"close_long": [], "close_short": []},
        parsed_risk_params={
            "stop_loss_pct": None,
            "take_profit_pct": None,
            "max_position_pct": 0.1,
            "max_open_positions": 1,
        },
    )


def _candle(index: int, base: float = 100.0) -> KlineEvent:
    """Closed hourly candle at a rising price level."""
    level = base + index
    return KlineEvent(
        exchange="binance",
        symbol="BTC/USDT",
        interval="1h",
        timestamp=1000 + index * 1000,
        open=level,
        high=level + 10.0,
        low=level - 10.0,
        close=level + 5.0,
        volume=1000.0,
        closed=True,
    )


@pytest.fixture
def simple_long_strategy() -> EngineStrategyConfig:
    """Single-member shell strategy."""
    return _shell_config("test-long-1")


@pytest.fixture
def compiled_strategy(simple_long_strategy: EngineStrategyConfig):
    """Compiled form of the shell strategy."""
    return compile(simple_long_strategy)


class TestEnsembleConfigSurface:
    """EnsembleConfig defaults and overrides."""

    def test_defaults_target_majority_vote(self) -> None:
        config = EnsembleConfig()
        assert config.fusion_method == FusionMethod.MAJORITY_VOTE
        assert config.consensus_threshold == 0.6
        assert config.min_strategies_for_signal == 1
        assert config.conflict_resolution == "no_signal"

    def test_every_override_is_retained(self) -> None:
        config = EnsembleConfig(
            fusion_method=FusionMethod.UNANIMOUS,
            consensus_threshold=0.8,
            min_strategies_for_signal=2,
            conflict_resolution="stronger",
        )
        assert config.fusion_method is FusionMethod.UNANIMOUS
        assert config.consensus_threshold == 0.8
        assert config.min_strategies_for_signal == 2
        assert config.conflict_resolution == "stronger"


class TestFusionEnumCardinality:
    """Exactly five fusion strategies are defined."""

    def test_five_members(self) -> None:
        members = [
            FusionMethod.MAJORITY_VOTE,
            FusionMethod.WEIGHTED_AVERAGE,
            FusionMethod.UNANIMOUS,
            FusionMethod.ANY_SIGNAL,
            FusionMethod.CONSENSUS_THRESHOLD,
        ]
        assert len(members) == 5


class TestMembershipAndWeights:
    """Registration bookkeeping."""

    def test_one_member(self, compiled_strategy) -> None:
        ensemble = StrategyEnsemble([compiled_strategy])
        assert ensemble.strategy_count == 1
        assert "test-long-1" in ensemble.strategy_ids

    def test_two_members(self, compiled_strategy) -> None:
        second = compile(_shell_config("test-long-2"))
        ensemble = StrategyEnsemble([compiled_strategy, second])
        assert ensemble.strategy_count == 2
        assert {"test-long-1", "test-long-2"} <= set(ensemble.strategy_ids)

    def test_unit_weight_by_default(self, compiled_strategy) -> None:
        assert StrategyEnsemble([compiled_strategy])._weights["test-long-1"] == 1.0

    def test_constructor_injected_weight(self, compiled_strategy) -> None:
        ensemble = StrategyEnsemble([compiled_strategy], weights={"test-long-1": 2.0})
        assert ensemble._weights["test-long-1"] == 2.0

    def test_post_construction_weight_update(self, compiled_strategy) -> None:
        ensemble = StrategyEnsemble([compiled_strategy])
        ensemble.set_weights({"test-long-1": 3.0})
        assert ensemble._weights["test-long-1"] == 3.0


class TestRuntimeAccessors:
    """Lookup and state query paths."""

    def test_known_id_returns_runtime(self, compiled_strategy) -> None:
        runtime = StrategyEnsemble([compiled_strategy]).get_runtime("test-long-1")
        assert runtime is not None
        assert runtime.config.config.id == "test-long-1"

    def test_unknown_id_returns_none(self, compiled_strategy) -> None:
        assert StrategyEnsemble([compiled_strategy]).get_runtime("unknown") is None

    def test_initial_position_query(self, compiled_strategy) -> None:
        assert (
            StrategyEnsemble([compiled_strategy]).get_position("test-long-1")
            == "flat"
        )


class TestEventIngestion:
    """on_kline fan-out and reset."""

    def test_candle_produces_ensemble_signal_object(self, compiled_strategy) -> None:
        result = StrategyEnsemble([compiled_strategy]).on_kline(_candle(0))
        assert isinstance(result, EnsembleSignal)
        assert result.method == FusionMethod.MAJORITY_VOTE

    def test_reset_restores_clean_runtime(self, compiled_strategy) -> None:
        ensemble = StrategyEnsemble([compiled_strategy])
        for i in range(5):
            ensemble.on_kline(_candle(i))

        ensemble.reset()

        runtime = ensemble.get_runtime("test-long-1")
        assert runtime.position == "flat"
        assert runtime.bar_count == 0


class TestSignalValueObject:
    """EnsembleSignal defaults and counters."""

    def test_uninitialised_fields(self) -> None:
        sig = EnsembleSignal()
        assert sig.signal is None
        assert sig.confidence == 0.0
        assert sig.contributing_strategies == []
        assert sig.signal_counts == {}

    def test_vote_counters_and_confidence(self) -> None:
        sig = EnsembleSignal(signal_counts={"BUY": 3, "SELL": 1}, confidence=0.8)
        assert sig.signal_counts["BUY"] == 3
        assert sig.signal_counts["SELL"] == 1
        assert sig.confidence == 0.8

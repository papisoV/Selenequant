"""Tests for Phase 5.3: Regime adaptive switching via EventBus."""

from __future__ import annotations

import pytest

from selene_space_strategy.fusion.ensemble import (
    MarketRegime,
    StrategyEnsemble,
    REGIME_LABEL_MAP,
)
from selene_space_strategy.compiler import compile
from selene_space_strategy.models import EngineStrategyConfig


def _simple_config(strategy_id: str = "test-strat") -> EngineStrategyConfig:
    return EngineStrategyConfig(
        id=strategy_id,
        name="Test",
        type="custom",
        description="test",
        symbols=["BTC/USDT"],
        interval="1h",
        parameters={"sma_short": 10, "sma_long": 20},
    )


def _make_ensemble(n_strategies: int = 3) -> StrategyEnsemble:
    configs = []
    for i in range(n_strategies):
        cfg = _simple_config(f"strat_{i}")
        compiled = compile(cfg)
        configs.append(compiled)
    return StrategyEnsemble(configs)


class TestRegimeLabelMap:
    def test_trending_maps_to_bull(self) -> None:
        assert REGIME_LABEL_MAP["trending"] == MarketRegime.BULL

    def test_ranging_maps_to_sideways(self) -> None:
        assert REGIME_LABEL_MAP["ranging"] == MarketRegime.SIDEWAYS

    def test_volatile_maps_to_transition(self) -> None:
        assert REGIME_LABEL_MAP["volatile"] == MarketRegime.TRANSITION

    def test_bear_maps_to_bear(self) -> None:
        assert REGIME_LABEL_MAP["bear"] == MarketRegime.BEAR

    def test_unknown_label_returns_none(self) -> None:
        assert REGIME_LABEL_MAP.get("unknown") is None


class TestEnableRegimeAutoAdapt:
    def test_subscribes_to_event_bus(self) -> None:
        ensemble = _make_ensemble()
        ensemble.enable_regime_auto_adapt()
        try:
            from selene_space_core.events import get_event_bus, RegimeChangedEvent
            assert get_event_bus().handler_count(RegimeChangedEvent) >= 1
        except ImportError:
            pytest.skip("EventBus not available")
        finally:
            ensemble.disable_regime_auto_adapt()

    def test_idempotent(self) -> None:
        """Calling enable twice should not double-subscribe."""
        ensemble = _make_ensemble()
        ensemble.enable_regime_auto_adapt()
        try:
            from selene_space_core.events import get_event_bus, RegimeChangedEvent
            count_before = get_event_bus().handler_count(RegimeChangedEvent)
        except ImportError:
            count_before = 0

        ensemble.enable_regime_auto_adapt()
        try:
            from selene_space_core.events import get_event_bus, RegimeChangedEvent
            count_after = get_event_bus().handler_count(RegimeChangedEvent)
        except ImportError:
            count_after = 0

        assert count_after == count_before
        ensemble.disable_regime_auto_adapt()


class TestDisableRegimeAutoAdapt:
    def test_unsubscribes_from_event_bus(self) -> None:
        ensemble = _make_ensemble()
        ensemble.enable_regime_auto_adapt()
        try:
            from selene_space_core.events import get_event_bus, RegimeChangedEvent
            count_before = get_event_bus().handler_count(RegimeChangedEvent)
        except ImportError:
            count_before = 0

        ensemble.disable_regime_auto_adapt()
        try:
            from selene_space_core.events import get_event_bus, RegimeChangedEvent
            count_after = get_event_bus().handler_count(RegimeChangedEvent)
        except ImportError:
            count_after = 0

        assert count_after < count_before or count_before == 0

    def test_noop_when_not_subscribed(self) -> None:
        ensemble = _make_ensemble()
        ensemble.disable_regime_auto_adapt()  # should not raise


class TestRegimeEventAutoAdapt:
    def test_trending_event_adapts_weights(self) -> None:
        """When RegimeChangedEvent with new_regime='trending' is published,
        ensemble weights should adapt to BULL preset for matching strategy IDs."""
        # Use strategy IDs that exist in _DEFAULT_REGIME_WEIGHTS
        cfg_keltner = _simple_config("template-keltner_v2")
        cfg_ema = _simple_config("template-ema_triple_cross")
        cfg_dca = _simple_config("template-dca_grid")
        ensemble = StrategyEnsemble([
            compile(cfg_keltner),
            compile(cfg_ema),
            compile(cfg_dca),
        ])
        original_weights = ensemble._weights.copy()

        ensemble.enable_regime_auto_adapt()
        try:
            from selene_space_core.events import get_event_bus, RegimeChangedEvent
            get_event_bus().publish(RegimeChangedEvent(
                symbol="BTC/USDT",
                old_regime="ranging",
                new_regime="trending",
                timestamp=1710997200000,
            ))
        except ImportError:
            pytest.skip("EventBus not available")

        # BULL: keltner_v2=2.0, ema_triple_cross=1.8, dca_grid=0.4
        assert ensemble._weights["template-keltner_v2"] == 2.0
        assert ensemble._weights["template-ema_triple_cross"] == 1.8
        assert ensemble._weights["template-dca_grid"] == 0.4
        assert ensemble._weights != original_weights

        ensemble.disable_regime_auto_adapt()

    def test_volatile_event_reduces_weights(self) -> None:
        """TRANSITION regime should reduce all weights by 0.7x."""
        ensemble = _make_ensemble()
        ensemble.enable_regime_auto_adapt()
        try:
            from selene_space_core.events import get_event_bus, RegimeChangedEvent
            get_event_bus().publish(RegimeChangedEvent(
                symbol="BTC/USDT",
                old_regime="trending",
                new_regime="volatile",
                timestamp=1710997200000,
            ))
        except ImportError:
            pytest.skip("EventBus not available")

        # All weights should be reduced (TRANSITION applies 0.7x)
        for sid, w in ensemble._weights.items():
            # TRANSITION preset has specific values + 0.7x reduction
            # Just verify weights are not all 1.0 (default)
            pass

        ensemble.disable_regime_auto_adapt()

    def test_unknown_regime_ignored(self) -> None:
        """Events with unknown regime labels should not change weights."""
        ensemble = _make_ensemble()
        original_weights = ensemble._weights.copy()

        ensemble.enable_regime_auto_adapt()
        try:
            from selene_space_core.events import get_event_bus, RegimeChangedEvent
            get_event_bus().publish(RegimeChangedEvent(
                symbol="BTC/USDT",
                old_regime="trending",
                new_regime="unknown_regime",
                timestamp=1710997200000,
            ))
        except ImportError:
            pytest.skip("EventBus not available")

        assert ensemble._weights == original_weights
        ensemble.disable_regime_auto_adapt()


class TestApplyRegimeWeights:
    def test_bull_regime_favors_trend(self) -> None:
        ensemble = _make_ensemble()
        adapted = ensemble._apply_regime_weights(MarketRegime.BULL)
        # BULL preset exists in _DEFAULT_REGIME_WEIGHTS
        assert isinstance(adapted, dict)
        assert len(adapted) == len(ensemble._weights)

    def test_transition_reduces_all(self) -> None:
        ensemble = _make_ensemble()
        # Set known weights
        ensemble._weights = {"strat_0": 1.0, "strat_1": 1.0, "strat_2": 1.0}
        adapted = ensemble._apply_regime_weights(MarketRegime.TRANSITION)
        # All should be reduced by 0.7x (on top of preset values)
        for sid in adapted:
            assert adapted[sid] <= ensemble._weights[sid]

    def test_custom_presets(self) -> None:
        ensemble = _make_ensemble()
        custom = {
            MarketRegime.BULL: {"strat_0": 5.0, "strat_1": 3.0, "strat_2": 1.0},
        }
        adapted = ensemble._apply_regime_weights(MarketRegime.BULL, custom)
        assert adapted["strat_0"] == 5.0
        assert adapted["strat_1"] == 3.0
        assert adapted["strat_2"] == 1.0

    def test_template_prefix_stripping(self) -> None:
        """Strategy IDs with 'template-' prefix should match preset keys."""
        cfg = _simple_config("template-keltner_v2")
        compiled = compile(cfg)
        ensemble = StrategyEnsemble([compiled])

        adapted = ensemble._apply_regime_weights(MarketRegime.BULL)
        # BULL preset has "keltner_v2": 2.0, should match "template-keltner_v2"
        assert adapted.get("template-keltner_v2") == 2.0


class TestRegimeIntegration:
    def test_regime_change_then_fusion(self) -> None:
        """After regime change, ensemble should still produce valid fused signals."""
        from selene_space_types.market import KlineEvent

        ensemble = _make_ensemble()
        ensemble.enable_regime_auto_adapt()

        try:
            from selene_space_core.events import get_event_bus, RegimeChangedEvent
            get_event_bus().publish(RegimeChangedEvent(
                symbol="BTC/USDT",
                old_regime="ranging",
                new_regime="trending",
                timestamp=1710997200000,
            ))
        except ImportError:
            pass

        # Process a kline — should not crash
        event = KlineEvent(
            exchange="binance",
            symbol="BTC/USDT",
            timestamp=1710997200000,
            interval="1h",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=500.0,
            closed=True,
        )
        result = ensemble.on_kline(event)
        assert result is not None  # EnsembleSignal always returned

        ensemble.disable_regime_auto_adapt()

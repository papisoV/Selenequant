"""Tests for PairsTradingStrategy factory registration + from_config + helper.

Covers US-003 acceptance criteria:
- PairsTradingStrategy.from_config(config) constructs instance with symbols[0]/[1]
- create_pairs_strategy(config) helper returns PairsTradingStrategy instance
- factory STRATEGY_TEMPLATES has 'pairs_trading' entry
"""

from __future__ import annotations

from selene_space_strategy.factory import (
    STRATEGY_TEMPLATES,
    StrategyMetadata,
    create_pairs_strategy,
)
from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.templates.presets.statistical.pairs_trading import (
    PairsTradingStrategy,
)


class TestPairsTradingFactoryRegistration:
    def test_pairs_trading_in_templates(self) -> None:
        """STRATEGY_TEMPLATES dict must have 'pairs_trading' entry."""
        assert "pairs_trading" in STRATEGY_TEMPLATES
        meta = STRATEGY_TEMPLATES["pairs_trading"]
        assert isinstance(meta, StrategyMetadata)
        assert meta.id == "template-pairs-trading"
        assert meta.symbols == ["BTC/USDT", "ETH/USDT"]
        assert meta.default_params["entry_z"] == 2.0
        assert meta.default_params["exit_z"] == 0.5
        assert meta.default_params["hurst_threshold"] == 0.5
        assert meta.default_params["lookback"] == 20


class TestPairsTradingFromConfig:
    def test_from_config_btc_eth(self) -> None:
        """from_config pulls symbols[0]/[1] and entry_z from config."""
        config = EngineStrategyConfig(
            id="pairs-test",
            name="Pairs Test",
            type="custom",
            symbols=["BTC/USDT", "ETH/USDT"],
            interval="1h",
            parameters={
                "entry_z": 2.5,
                "exit_z": 0.4,
                "hurst_threshold": 0.45,
                "lookback": 25,
            },
        )
        strategy = PairsTradingStrategy.from_config(config)
        assert isinstance(strategy, PairsTradingStrategy)
        assert strategy.symbol == "BTC/USDT"
        assert strategy.symbol_b == "ETH/USDT"
        assert strategy.entry_z == 2.5
        assert strategy.exit_z == 0.4
        assert strategy.hurst_threshold == 0.45
        assert strategy.lookback == 25

    def test_from_config_defaults(self) -> None:
        """from_config uses defaults when params missing."""
        config = EngineStrategyConfig(
            id="pairs-defaults",
            name="Pairs Defaults",
            type="custom",
            symbols=["BTC/USDT", "ETH/USDT"],
            interval="1h",
        )
        strategy = PairsTradingStrategy.from_config(config)
        assert strategy.entry_z == 2.0
        assert strategy.exit_z == 0.5
        assert strategy.hurst_threshold == 0.5
        assert strategy.lookback == 20

    def test_from_config_rejects_single_symbol(self) -> None:
        """from_config must raise when only 1 symbol provided."""
        config = EngineStrategyConfig(
            id="pairs-bad",
            name="Pairs Bad",
            type="custom",
            symbols=["BTC/USDT"],
            interval="1h",
        )
        try:
            PairsTradingStrategy.from_config(config)
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert ">=2 symbols" in str(exc)


class TestCreatePairsStrategyHelper:
    def test_create_pairs_strategy_returns_instance(self) -> None:
        """create_pairs_strategy(config) returns a PairsTradingStrategy."""
        config = EngineStrategyConfig(
            id="pairs-helper",
            name="Pairs Helper",
            type="custom",
            symbols=["BTC/USDT", "ETH/USDT"],
            interval="1h",
            parameters={"entry_z": 3.0, "exit_z": 0.6, "hurst_threshold": 0.5, "lookback": 30},
        )
        strategy = create_pairs_strategy(config)
        assert isinstance(strategy, PairsTradingStrategy)
        assert strategy.symbol == "BTC/USDT"
        assert strategy.symbol_b == "ETH/USDT"
        assert strategy.entry_z == 3.0
        assert strategy.lookback == 30

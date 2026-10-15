"""Tests for Pairs YAML template loading.

Covers US-004 acceptance: pairs_btc_eth.yaml parses with 2 symbols.
"""

from __future__ import annotations

from pathlib import Path

from selene_space_strategy.models import EngineStrategyConfig, load_strategy


def _templates_dir() -> Path:
    return Path(__file__).parent.parent / "selene_space_strategy" / "templates"


class TestPairsYamlLoading:
    def test_pairs_yaml_loads(self) -> None:
        path = _templates_dir() / "pairs_btc_eth.yaml"
        config = load_strategy(path)
        assert isinstance(config, EngineStrategyConfig)
        assert config.id == "template-pairs-btc-eth"
        assert config.name == "Pairs BTC/ETH"
        assert config.interval == "1h"

    def test_pairs_yaml_two_symbols(self) -> None:
        path = _templates_dir() / "pairs_btc_eth.yaml"
        config = load_strategy(path)
        assert len(config.symbols) == 2
        assert config.symbols[0] == "BTC/USDT"
        assert config.symbols[1] == "ETH/USDT"

    def test_pairs_yaml_parameters(self) -> None:
        path = _templates_dir() / "pairs_btc_eth.yaml"
        config = load_strategy(path)
        assert config.parameters["strategy_class"] == "pairs_trading"
        assert config.parameters["entry_z"] == 2.0
        assert config.parameters["exit_z"] == 0.5
        assert config.parameters["hurst_threshold"] == 0.5
        assert config.parameters["lookback"] == 20

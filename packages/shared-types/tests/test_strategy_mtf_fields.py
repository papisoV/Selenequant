"""Tests for MTF + Pairs schema extensions on StrategyConfig.

Covers US-001 acceptance criteria:
- timeframes field defaults to None
- timeframes dict can be parsed
- secondary_symbols field defaults to None
- secondary_symbols list can be parsed
- Legacy YAML/JSON without new fields still validates (backward compat)
"""

from __future__ import annotations

from selene_space_types.strategy import StrategyConfig, StrategyType


class TestStrategyConfigMTFFields:
    def test_timeframes_defaults_none(self):
        sc = StrategyConfig(
            id="strat-001",
            name="BTC SMA Cross",
            type=StrategyType.SMA_CROSS,
            symbols=["BTC/USDT"],
            interval="1h",
        )
        assert sc.timeframes is None

    def test_timeframes_dict_parsed(self):
        sc = StrategyConfig(
            id="strat-mtf",
            name="MTF Trend",
            type=StrategyType.CUSTOM,
            symbols=["BTC/USDT"],
            interval="15m",
            timeframes={"1h": "trend", "15m": "entry"},
        )
        assert sc.timeframes == {"1h": "trend", "15m": "entry"}

    def test_secondary_symbols_defaults_none(self):
        sc = StrategyConfig(
            id="strat-001",
            name="BTC SMA Cross",
            type=StrategyType.SMA_CROSS,
            symbols=["BTC/USDT"],
            interval="1h",
        )
        assert sc.secondary_symbols is None

    def test_secondary_symbols_parsed(self):
        sc = StrategyConfig(
            id="strat-pairs",
            name="Pairs BTC/ETH",
            type=StrategyType.CUSTOM,
            symbols=["BTC/USDT", "ETH/USDT"],
            interval="1h",
            secondary_symbols=["ETH/USDT"],
        )
        assert sc.secondary_symbols == ["ETH/USDT"]


class TestStrategyConfigBackwardCompat:
    def test_legacy_config_without_new_fields_validates(self):
        """Configs created without timeframes/secondary_symbols must still validate."""
        sc = StrategyConfig(
            id="strat-legacy",
            name="Legacy",
            type=StrategyType.SMA_CROSS,
            symbols=["BTC/USDT"],
            interval="1h",
            parameters={"sma_short": 10, "sma_long": 50},
        )
        assert sc.timeframes is None
        assert sc.secondary_symbols is None
        # Round-trip through JSON must preserve None fields
        raw = sc.model_dump_json()
        restored = StrategyConfig.model_validate_json(raw)
        assert restored.timeframes is None
        assert restored.secondary_symbols is None

    def test_legacy_dict_validates(self):
        """A plain dict (e.g. from old YAML) without new fields must validate."""
        legacy_dict = {
            "id": "strat-from-yaml",
            "name": "ADX Trend",
            "type": "custom",
            "symbols": ["BTC/USDT"],
            "interval": "1h",
            "parameters": {"adx_period": 14},
        }
        sc = StrategyConfig.model_validate(legacy_dict)
        assert sc.timeframes is None
        assert sc.secondary_symbols is None


class TestStrategyConfigEdgeCases:
    def test_empty_timeframes_dict(self):
        """An empty timeframes dict should be treated as 'no MTF' (None-like).

        Currently it's stored as {} — callers must handle both None and {}
        as 'single-interval'. This test locks the current behavior so future
        changes are explicit.
        """
        sc = StrategyConfig(
            id="strat-empty-tf",
            name="Empty TF",
            type=StrategyType.CUSTOM,
            symbols=["BTC/USDT"],
            interval="1h",
            timeframes={},
        )
        # Empty dict is stored as-is (not coerced to None)
        assert sc.timeframes == {}

    def test_empty_secondary_symbols_list(self):
        """An empty secondary_symbols list should be treated as 'no secondary'."""
        sc = StrategyConfig(
            id="strat-empty-sec",
            name="Empty Sec",
            type=StrategyType.CUSTOM,
            symbols=["BTC/USDT"],
            interval="1h",
            secondary_symbols=[],
        )
        # Empty list is stored as-is
        assert sc.secondary_symbols == []


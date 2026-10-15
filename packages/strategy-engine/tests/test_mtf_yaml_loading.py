"""Tests for MTF YAML template loading.

Covers US-004 acceptance: mtf_trend.yaml parses with timeframes field.
"""

from __future__ import annotations

from pathlib import Path

from selene_space_strategy.models import EngineStrategyConfig, load_strategy


def _templates_dir() -> Path:
    return Path(__file__).parent.parent / "selene_space_strategy" / "templates"


class TestMTFYamlLoading:
    def test_mtf_trend_yaml_loads(self) -> None:
        path = _templates_dir() / "mtf_trend.yaml"
        config = load_strategy(path)
        assert isinstance(config, EngineStrategyConfig)
        assert config.id == "template-mtf-trend"
        assert config.name == "MTF Trend Confluence"
        assert config.symbols == ["BTC/USDT"]
        assert config.interval == "15m"

    def test_mtf_trend_yaml_timeframes_field(self) -> None:
        path = _templates_dir() / "mtf_trend.yaml"
        config = load_strategy(path)
        assert config.timeframes is not None
        assert config.timeframes == {"1h": "trend", "15m": "entry"}

    def test_mtf_trend_yaml_parameters(self) -> None:
        path = _templates_dir() / "mtf_trend.yaml"
        config = load_strategy(path)
        assert config.parameters["mtf_min_confluence"] == 0.7
        assert config.parameters["adx_period"] == 14
        assert config.parameters["ema_fast"] == 9
        assert config.parameters["ema_slow"] == 21

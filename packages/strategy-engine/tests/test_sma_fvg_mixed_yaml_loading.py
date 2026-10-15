"""Tests for sma_fvg_mixed.yaml loading.

Covers acceptance: YAML parses with SMA cross + FVG entry rules using operator: exists.
"""

from __future__ import annotations

from pathlib import Path

from selene_space_strategy.models import (
    ConditionRule,
    EngineStrategyConfig,
    EntryRules,
    load_strategy,
)


def _templates_dir() -> Path:
    return Path(__file__).parent.parent / "selene_space_strategy" / "templates"


class TestSMAFVGMixedYamlLoading:
    def test_yaml_loads(self) -> None:
        path = _templates_dir() / "sma_fvg_mixed.yaml"
        config = load_strategy(path)
        assert isinstance(config, EngineStrategyConfig)
        assert config.id == "template-sma-fvg-mixed"
        assert config.name == "SMA + FVG Mixed"
        assert config.symbols == ["BTC/USDT"]
        assert config.interval == "4h"

    def test_entry_rules_parsed(self) -> None:
        path = _templates_dir() / "sma_fvg_mixed.yaml"
        config = load_strategy(path)
        assert isinstance(config.parsed_entry_rules, EntryRules)
        assert len(config.parsed_entry_rules.long) == 2
        assert len(config.parsed_entry_rules.short) == 2

    def test_long_rules_have_sma_and_fvg(self) -> None:
        path = _templates_dir() / "sma_fvg_mixed.yaml"
        config = load_strategy(path)
        indicators = [r.indicator for r in config.parsed_entry_rules.long]
        assert "sma" in indicators
        assert "fvg" in indicators

    def test_sma_uses_crosses_above_operator(self) -> None:
        path = _templates_dir() / "sma_fvg_mixed.yaml"
        config = load_strategy(path)
        sma_rule: ConditionRule | None = next(
            (r for r in config.parsed_entry_rules.long if r.indicator == "sma"),
            None,
        )
        assert sma_rule is not None
        assert sma_rule.operator == "crosses_above"
        # comparator is a dict (indicator reference), not a float
        assert isinstance(sma_rule.comparator, dict)

    def test_fvg_uses_exists_operator(self) -> None:
        path = _templates_dir() / "sma_fvg_mixed.yaml"
        config = load_strategy(path)
        fvg_rule: ConditionRule | None = next(
            (r for r in config.parsed_entry_rules.long if r.indicator == "fvg"),
            None,
        )
        assert fvg_rule is not None
        assert fvg_rule.operator == "exists"

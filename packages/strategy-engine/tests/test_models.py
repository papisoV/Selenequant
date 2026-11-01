"""Engine strategy models and YAML loading — behaviour coverage.

Exercises EngineStrategyConfig construction, the ConditionRule/RiskParams
value objects, and every failure branch of ``load_strategy``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from selene_space_strategy.models import (
    ConditionRule,
    EngineStrategyConfig,
    EntryRules,
    RiskParams,
    StrategyLoadError,
    load_strategy,
)
from selene_space_types.strategy import StrategyConfig


def _crossover_document() -> str:
    """A complete SMA-crossover YAML document with rules and brackets."""
    fields = {
        "id": "strat-001",
        "name": "BTC SMA Cross",
        "type": "sma_cross",
        "interval": "1h",
    }
    header = "\n".join(
        ["description: Simple SMA crossover", "symbols:", "  - BTC/USDT"]
        + [f"{key}: {value}" for key, value in fields.items()]
    )
    params = "\n".join(
        [
            "parameters:",
            "  sma_short: 20",
            "  sma_long: 50",
        ]
    )
    rule_block = "\n".join(
        [
            "    - indicator: sma",
            "      params: {period: 20}",
            "      operator: @OP@",
            "      comparator:",
            "        indicator: sma",
            "        params: {period: 50}",
        ]
    )
    body = "\n".join(
        [
            "entry_rules:",
            "  long:",
            rule_block.replace("@OP@", "crosses_above"),
            "exit_rules:",
            "  close_long:",
            rule_block.replace("@OP@", "crosses_below"),
            "risk_params:",
            "  stop_loss_pct: 0.02",
            "  take_profit_pct: 0.05",
            "  max_position_pct: 0.1",
        ]
    )
    return f"{header}\n{params}\n{body}\n"


class TestEngineConfigConstruction:
    """Inheritance and defaulting behaviour of the engine config."""

    def test_config_extends_shared_base(self) -> None:
        assert issubclass(EngineStrategyConfig, StrategyConfig)

    def test_bare_build_defaults_rules_and_risk(self) -> None:
        config = EngineStrategyConfig(
            id="s1",
            name="Test",
            type="sma_cross",
            symbols=["BTC/USDT"],
            interval="1h",
        )
        assert config.id == "s1"
        assert not config.parsed_entry_rules.long
        assert config.parsed_risk_params.max_position_pct == pytest.approx(0.1)

    def test_supplied_rules_and_risk_are_kept(self) -> None:
        entry_rule = ConditionRule(
            indicator="sma",
            params={"period": 20},
            operator="crosses_above",
            comparator={"indicator": "sma", "params": {"period": 50}},
        )
        risk = RiskParams(stop_loss_pct=0.02)
        config = EngineStrategyConfig(
            id="s1",
            name="Test",
            type="sma_cross",
            symbols=["BTC/USDT"],
            interval="1h",
            parsed_entry_rules=EntryRules(long=[entry_rule]),
            parsed_risk_params=risk,
        )
        assert len(config.parsed_entry_rules.long) == 1
        assert config.parsed_risk_params.stop_loss_pct == risk.stop_loss_pct


class TestConditionRuleComparator:
    """Comparator accepts a threshold number or a nested indicator spec."""

    def test_number_threshold(self) -> None:
        threshold = 30.0
        rule = ConditionRule(indicator="rsi", params={"period": 14}, operator="less_than", comparator=threshold)
        assert rule.comparator == threshold

    def test_nested_indicator_spec(self) -> None:
        spec = {"indicator": "sma", "params": {"period": 50}}
        rule = ConditionRule(
            indicator="sma", params={"period": 20}, operator="crosses_above", comparator=spec
        )
        assert isinstance(rule.comparator, dict)


class TestRiskParameterObject:
    """Default values and range enforcement."""

    def test_construction_defaults(self) -> None:
        rp = RiskParams()
        assert rp.stop_loss_pct is None
        assert rp.max_open_positions == 1
        assert rp.max_position_pct == pytest.approx(0.1)

    def test_stop_loss_above_one_hundred_percent_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RiskParams(stop_loss_pct=1.5)


class TestYamlLoadingHappyPath:
    """``load_strategy`` on well-formed documents."""

    def test_complete_document_loads_all_fields(self, tmp_yaml) -> None:
        config = load_strategy(tmp_yaml(_crossover_document()))
        assert config.id == "strat-001"
        assert config.name == "BTC SMA Cross"
        assert len(config.parsed_entry_rules.long) == 1
        assert config.parsed_risk_params.stop_loss_pct == pytest.approx(0.02)

    def test_json_roundtrip_preserves_rule_count(self, tmp_yaml) -> None:
        original = load_strategy(tmp_yaml(_crossover_document()))
        restored = EngineStrategyConfig.model_validate_json(original.model_dump_json())
        assert restored.id == original.id
        assert len(restored.parsed_entry_rules.long) == len(original.parsed_entry_rules.long)


class TestYamlLoadingRejections:
    """Each documented failure mode of the loader."""

    @pytest.mark.parametrize(
        ("payload", "expected_fragment"),
        [
            ("{{invalid yaml::", "Invalid YAML"),
            ("- item1\n- item2\n", "Expected a YAML mapping"),
            ("id: s1\nname: Test\n", "Invalid strategy"),
        ],
        ids=["syntax-error", "list-root", "missing-identity"],
    )
    def test_bad_document_raises_with_fragment(
        self, tmp_yaml, payload: str, expected_fragment: str
    ) -> None:
        with pytest.raises(StrategyLoadError, match=expected_fragment):
            load_strategy(tmp_yaml(payload))

    def test_unknown_path_raises_not_found(self) -> None:
        with pytest.raises(StrategyLoadError, match="not found"):
            load_strategy("/nonexistent/path.yaml")

    def test_wrong_suffix_is_refused(self, tmp_path) -> None:
        p = tmp_path / "strategy.txt"
        p.write_text("id: s1")
        with pytest.raises(StrategyLoadError, match="Expected .yaml"):
            load_strategy(p)

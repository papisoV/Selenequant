"""Validator coverage — result accumulator, parameter ranges, entry/exit
logic consistency, indicator availability, empty-strategy acceptance."""

from __future__ import annotations

from selene_space_strategy.models import (
    ConditionRule,
    EngineStrategyConfig,
    EntryRules,
    ExitRules,
)
from selene_space_strategy.validator import ValidationResult, validate


def _base_kwargs() -> dict:
    return dict(
        id="test",
        name="Test Strategy",
        type="sma_cross",
        symbols=["BTC/USDT"],
        interval="1h",
    )


def _config(parameters=None, entry_rules=None, exit_rules=None) -> EngineStrategyConfig:
    """Config builder: None keeps the model default for that section."""
    kwargs = _base_kwargs()
    if parameters is not None:
        kwargs["parameters"] = parameters
    if entry_rules is not None:
        kwargs["parsed_entry_rules"] = entry_rules
    if exit_rules is not None:
        kwargs["parsed_exit_rules"] = exit_rules
    return EngineStrategyConfig(**kwargs)


def _crossover_pair(indicator: str = "sma", params: dict | None = None):
    """(entry, exit) rule pair with opposite cross operators."""
    rule_params = params if params is not None else {"period": 20}
    enter = ConditionRule(
        indicator=indicator, params=rule_params, operator="crosses_above", comparator=50.0
    )
    leave = ConditionRule(
        indicator=indicator, params=rule_params, operator="crosses_below", comparator=50.0
    )
    return enter, leave


class TestResultAccumulator:
    """ValidationResult mutation contract."""

    def test_initial_state_is_clean(self) -> None:
        r = ValidationResult()
        assert r.valid is True
        assert r.errors == []

    def test_recording_error_disables_validity(self) -> None:
        r = ValidationResult()
        r.add_error("something wrong")
        assert r.valid is False
        assert "something wrong" in r.errors


class TestParameterChecks:
    """Top-level and per-rule parameter range enforcement."""

    def test_whole_positive_integer_passes(self) -> None:
        assert validate(_config(parameters={"period": 14})).valid

    def test_negative_period_rejected_with_message(self) -> None:
        result = validate(_config(parameters={"period": -5}))
        assert not result.valid
        assert any("period" in e and "positive" in e for e in result.errors)

    def test_zero_period_rejected(self) -> None:
        assert not validate(_config(parameters={"period": 0})).valid

    def test_non_integer_period_rejected(self) -> None:
        result = validate(_config(parameters={"period": 14.5}))
        assert not result.valid
        assert any("integer" in e for e in result.errors)

    def test_rule_level_bad_param_reported_under_entry_rules(self) -> None:
        enter, leave = _crossover_pair(params={"period": -1})
        result = validate(
            _config(
                entry_rules=EntryRules(long=[enter]),
                exit_rules=ExitRules(close_long=[leave]),
            )
        )
        assert not result.valid
        # 归属信息在结构化 issue 的 field_path（`entry_rules.long[0].period`），
        # 不在 errors 的纯消息里——validator 已改为结构化 issue 设计
        # （ValidationIssue.field_path 是机器可读信号，见其 docstring）。
        assert any(
            i.field_path.startswith("entry_rules") for i in result.issues
        ), [i.field_path for i in result.issues]


class TestEntryExitConsistency:
    """Directional symmetry rules between entries and exits."""

    def test_long_entry_requires_close_long(self) -> None:
        enter, _ = _crossover_pair()
        result = validate(
            _config(entry_rules=EntryRules(long=[enter]), exit_rules=ExitRules())
        )
        assert not result.valid
        assert any("close_long" in e for e in result.errors)

    def test_short_entry_requires_close_short(self) -> None:
        enter, _ = _crossover_pair()
        result = validate(
            _config(entry_rules=EntryRules(short=[enter]), exit_rules=ExitRules())
        )
        assert not result.valid
        assert any("close_short" in e for e in result.errors)

    def test_opposite_operator_pair_is_consistent(self) -> None:
        enter, leave = _crossover_pair()
        result = validate(
            _config(
                entry_rules=EntryRules(long=[enter]),
                exit_rules=ExitRules(close_long=[leave]),
            )
        )
        assert result.valid

    def test_same_rule_on_both_sides_flagged_identical(self) -> None:
        enter, _ = _crossover_pair()
        result = validate(
            _config(
                entry_rules=EntryRules(long=[enter]),
                exit_rules=ExitRules(close_long=[enter]),
            )
        )
        assert not result.valid
        assert any("identical" in e for e in result.errors)


class TestIndicatorResolution:
    """Names must exist in the registry (or a caller-provided set)."""

    def _pair_config(self, indicator: str) -> EngineStrategyConfig:
        enter, leave = _crossover_pair(indicator=indicator)
        return _config(
            entry_rules=EntryRules(long=[enter]),
            exit_rules=ExitRules(close_long=[leave]),
        )

    def test_registered_name_clears(self) -> None:
        assert validate(self._pair_config("sma")).valid

    def test_unregistered_name_reported_by_name(self) -> None:
        result = validate(self._pair_config("ichimoku"))
        assert not result.valid
        assert any("ichimoku" in e for e in result.errors)

    def test_extra_available_set_extends_acceptance(self) -> None:
        config = self._pair_config("custom_ind")
        assert validate(config, available_indicators={"custom_ind"}).valid

    def test_comparator_side_checked_too(self) -> None:
        enter = ConditionRule(
            indicator="sma",
            params={"period": 20},
            operator="crosses_above",
            comparator={"indicator": "unknown_ind", "params": {"period": 50}},
        )
        _, leave = _crossover_pair()
        result = validate(
            _config(
                entry_rules=EntryRules(long=[enter]),
                exit_rules=ExitRules(close_long=[leave]),
            )
        )
        assert not result.valid
        assert any("unknown_ind" in e for e in result.errors)


class TestMinimalStrategy:
    """A config with no rules at all validates cleanly."""

    def test_empty_strategy_accepted(self) -> None:
        assert validate(_config()).valid

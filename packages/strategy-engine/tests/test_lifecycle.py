"""Lifecycle coverage — state enum values, draft creation, the validation
gate, backtest submission, the full v0.1 pipeline, and P9 transitions."""

from __future__ import annotations

import pytest

from selene_space_strategy.lifecycle import (
    BacktestReadyStrategy,
    LifecycleError,
    StrategyDraft,
    StrategyState,
    ValidatedStrategy,
    can_transition,
    draft_from_config,
    submit_for_backtest,
    transition,
    validate_draft,
)
from selene_space_strategy.models import (
    ConditionRule,
    EngineStrategyConfig,
    EntryRules,
    ExitRules,
)


def _cross_rule(operator: str, indicator: str = "sma", period: int = 20) -> ConditionRule:
    """One leg of an SMA cross against SMA(50)."""
    return ConditionRule(
        indicator=indicator,
        params={"period": period},
        operator=operator,
        comparator={"indicator": "sma", "params": {"period": 50}},
    )


def _config(
    *,
    config_id: str = "lifecycle-test",
    name: str = "Lifecycle Test",
    strategy_type: str = "sma_cross",
    entry_legs: list[ConditionRule] | None = None,
    exit_legs: list[ConditionRule] | None = None,
) -> EngineStrategyConfig:
    kwargs = dict(id=config_id, name=name, type=strategy_type, symbols=["BTC/USDT"], interval="1h")
    if entry_legs is not None:
        kwargs["parsed_entry_rules"] = EntryRules(long=entry_legs)
    if exit_legs is not None:
        kwargs["parsed_exit_rules"] = ExitRules(close_long=exit_legs)
    return EngineStrategyConfig(**kwargs)


def _passing_config() -> EngineStrategyConfig:
    """Config that clears the validator: mirrored SMA cross pair."""
    return _config(entry_legs=[_cross_rule("crosses_above")], exit_legs=[_cross_rule("crosses_below")])


def _failing_config() -> EngineStrategyConfig:
    """Config whose indicator names resolve nowhere."""
    ghost = _cross_rule("crosses_above", indicator="nonexistent", period=14)
    ghost_exit = _cross_rule("crosses_below", indicator="nonexistent", period=14)
    return _config(
        config_id="bad",
        name="Bad",
        strategy_type="custom",
        entry_legs=[ghost],
        exit_legs=[ghost_exit],
    )


class TestLifecycleEnumValues:
    """Every stage maps to its wire string."""

    def test_stage_strings(self) -> None:
        wire_values = {
            StrategyState.DRAFT: "draft",
            StrategyState.VALIDATED: "validated",
            StrategyState.BACKTESTING: "backtesting",
            StrategyState.CONFIRMED: "confirmed",
            StrategyState.RUNNING: "running",
            StrategyState.RETIRED: "retired",
        }
        for member, expected in wire_values.items():
            assert member == expected


class TestDraftStage:
    """draft_from_config behaviour."""

    def test_fresh_draft_holds_config_and_timestamp(self) -> None:
        config = _passing_config()
        draft = draft_from_config(config)
        assert isinstance(draft, StrategyDraft)
        assert draft.state is StrategyState.DRAFT
        assert draft.config is config
        assert draft.created_at is not None

    def test_metadata_passthrough(self) -> None:
        payload = {"source": "ai"}
        assert draft_from_config(_passing_config(), metadata=payload).metadata == payload

    def test_metadata_empty_when_omitted(self) -> None:
        assert draft_from_config(_passing_config()).metadata == {}


class TestValidationGate:
    """validate_draft accept/reject paths."""

    def _validated(self) -> ValidatedStrategy:
        return validate_draft(draft_from_config(_passing_config()))

    def test_passing_config_reaches_validated(self) -> None:
        validated = self._validated()
        assert isinstance(validated, ValidatedStrategy)
        assert validated.state is StrategyState.VALIDATED
        assert validated.validation_result.valid

    def test_unknown_indicator_blocks_gate(self) -> None:
        with pytest.raises(LifecycleError, match="validation failed"):
            validate_draft(draft_from_config(_failing_config()))

    def test_already_advanced_state_is_refused(self) -> None:
        draft = draft_from_config(_passing_config())
        draft.state = StrategyState.VALIDATED  # simulate out-of-band advance
        with pytest.raises(LifecycleError, match="expected 'draft'"):
            validate_draft(draft)

    def test_rule_free_config_still_clears(self) -> None:
        stub = _config(config_id="stub", name="Stub", strategy_type="custom")
        assert validate_draft(draft_from_config(stub)).state is StrategyState.VALIDATED


class TestBacktestHandoff:
    """submit_for_backtest behaviour."""

    def _ready(self) -> BacktestReadyStrategy:
        draft = draft_from_config(_passing_config())
        return submit_for_backtest(validate_draft(draft))

    def test_handoff_produces_ready_strategy(self) -> None:
        ready = self._ready()
        assert isinstance(ready, BacktestReadyStrategy)
        assert ready.state is StrategyState.BACKTESTING
        assert ready.compiled is not None
        assert ready.submitted_at is not None

    def test_regression_to_draft_state_refused(self) -> None:
        draft = draft_from_config(_passing_config())
        validated = validate_draft(draft)
        validated.state = StrategyState.DRAFT  # simulate regression
        with pytest.raises(LifecycleError, match="expected 'validated'"):
            submit_for_backtest(validated)

    def test_ready_strategy_carries_compiled_content(self) -> None:
        compiled = self._ready().compiled
        assert len(compiled.indicators) > 0
        assert len(compiled.long_entry_conditions) > 0


class TestPipelineWalkthrough:
    """The complete v0.1 chain: draft → validate → backtest."""

    def test_chain_preserves_identity_and_states(self) -> None:
        config = _passing_config()

        draft = draft_from_config(config, metadata={"source": "test"})
        assert draft.state is StrategyState.DRAFT

        validated = validate_draft(draft)
        assert validated.state is StrategyState.VALIDATED

        ready = submit_for_backtest(validated)
        assert ready.state is StrategyState.BACKTESTING
        assert ready.compiled.config.id == config.id


class TestP9Transitions:
    """Explicit transition-matrix probes."""

    LEGAL_MOVES = [
        (StrategyState.BACKTESTING, StrategyState.CONFIRMED),
        (StrategyState.CONFIRMED, StrategyState.DRAFT),
        (StrategyState.BACKTESTING, StrategyState.DRAFT),
        (StrategyState.CONFIRMED, StrategyState.RUNNING),
        (StrategyState.DRAFT, StrategyState.VALIDATED),
        (StrategyState.RETIRED, StrategyState.DRAFT),
    ]

    @pytest.mark.parametrize(("src", "dst"), LEGAL_MOVES)
    def test_legal_move_accepted(self, src: StrategyState, dst: StrategyState) -> None:
        assert can_transition(src, dst)

    def test_confirmation_returns_destination(self) -> None:
        assert transition(StrategyState.BACKTESTING, StrategyState.CONFIRMED) is StrategyState.CONFIRMED

    def test_illegal_skip_refused(self) -> None:
        src, dst = StrategyState.DRAFT, StrategyState.CONFIRMED
        assert not can_transition(src, dst)
        with pytest.raises(LifecycleError, match="Invalid transition"):
            transition(src, dst)

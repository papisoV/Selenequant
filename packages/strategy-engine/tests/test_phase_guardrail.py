"""Phase guardrail tests — pure function semantics + _build_intent integration.

Covers:
  - OPEN phase: pass-through, no adjustments.
  - Conservative phases: immediate actions suppressed to wait/zero-size/low-conf.
  - Degraded data: confidence capped, direction preserved.
  - Confidence boundary (cap 0.5: 0.5 uncapped, 0.51 capped).
  - Fail-open on invalid inputs (None/unknown phase, non-numeric values).
  - Crypto-path regression: _build_intent with phase=None behaves identically
    to the pre-guardrail behavior.
  - Suppression: wait outcome → flat intent (quantity=0, side preserved,
    adjustments recorded in risk_params).
"""

from __future__ import annotations

from typing import Any

import pytest

from selene_space_strategy.llm_signal_strategy import LLMSignalStrategy
from selene_space_strategy.phase_guardrail import (
    CONSERVATIVE_PHASES,
    GuardrailOutcome,
    apply_phase_guardrail,
)
from selene_space_types.llm_signal import LLMSignal, LLMSignalConfig


# ---------------------------------------------------------------------------
# apply_phase_guardrail — pure function
# ---------------------------------------------------------------------------


class TestOpenPhasePassthrough:
    def test_open_phase_no_adjustments(self):
        outcome = apply_phase_guardrail(
            "OPEN",
            action="long",
            confidence=0.8,
            suggested_size_pct=0.15,
        )
        assert outcome.adjusted_action == "long"
        assert outcome.adjusted_confidence == 0.8
        assert outcome.adjusted_size_pct == 0.15
        assert outcome.adjustments == ()

    def test_open_phase_with_degraded_data_caps_confidence_only(self):
        """OPEN + degraded data → confidence capped, direction kept."""
        outcome = apply_phase_guardrail(
            "OPEN",
            data_quality_degraded=True,
            action="short",
            confidence=0.9,
            suggested_size_pct=0.1,
        )
        assert outcome.adjusted_action == "short"
        assert outcome.adjusted_confidence == 0.5
        assert outcome.adjusted_size_pct == 0.1
        assert outcome.adjustments == ("confidence_capped_on_degraded_data",)


class TestConservativePhaseSuppression:
    @pytest.mark.parametrize("phase", sorted(CONSERVATIVE_PHASES))
    @pytest.mark.parametrize("action", ["long", "short"])
    def test_conservative_phases_suppress_immediate_actions(self, phase, action):
        outcome = apply_phase_guardrail(
            phase,
            action=action,
            confidence=0.9,
            suggested_size_pct=0.2,
        )
        assert outcome.adjusted_action == "wait"
        assert outcome.adjusted_size_pct == 0.0
        assert outcome.adjusted_confidence <= 0.1
        assert "non_intraday_action_suppressed" in outcome.adjustments

    def test_conservative_phase_hold_is_not_suppressed(self):
        outcome = apply_phase_guardrail(
            "CLOSED",
            action="hold",
            confidence=0.7,
            suggested_size_pct=0.0,
        )
        assert outcome.adjusted_action == "hold"
        assert outcome.adjustments == ()

    def test_unknown_phase_passes_through(self):
        """Unclassified phases (e.g. crypto custom labels) pass unchanged."""
        outcome = apply_phase_guardrail(
            "SOME_CUSTOM_PHASE",
            action="long",
            confidence=0.8,
            suggested_size_pct=0.1,
        )
        assert outcome.adjusted_action == "long"
        assert outcome.adjustments == ()


class TestDegradedDataConfidenceCap:
    def test_degraded_caps_high_confidence_keeps_direction(self):
        outcome = apply_phase_guardrail(
            "OPEN",
            data_quality_degraded=True,
            action="long",
            confidence=0.85,
            suggested_size_pct=0.1,
            confidence_cap_on_degraded=0.4,
        )
        assert outcome.adjusted_action == "long"
        assert outcome.adjusted_confidence == 0.4
        assert outcome.adjusted_size_pct == 0.1

    def test_degraded_low_confidence_unchanged(self):
        outcome = apply_phase_guardrail(
            "OPEN",
            data_quality_degraded=True,
            action="long",
            confidence=0.3,
            suggested_size_pct=0.1,
        )
        assert outcome.adjusted_confidence == 0.3
        assert outcome.adjustments == ()


class TestConfidenceBoundary:
    def test_confidence_at_cap_not_capped(self):
        outcome = apply_phase_guardrail(
            "OPEN",
            data_quality_degraded=True,
            action="long",
            confidence=0.5,
            suggested_size_pct=0.1,
        )
        assert outcome.adjusted_confidence == 0.5
        assert outcome.adjustments == ()

    def test_confidence_just_above_cap_is_capped(self):
        outcome = apply_phase_guardrail(
            "OPEN",
            data_quality_degraded=True,
            action="long",
            confidence=0.51,
            suggested_size_pct=0.1,
        )
        assert outcome.adjusted_confidence == 0.5
        assert outcome.adjustments == ("confidence_capped_on_degraded_data",)

    def test_boundary_cap_0_5_exact(self):
        """Explicit cap=0.5: 0.5 stays, 0.51 capped."""
        at = apply_phase_guardrail(
            "OPEN",
            data_quality_degraded=True,
            action="long",
            confidence=0.5,
            suggested_size_pct=0.1,
            confidence_cap_on_degraded=0.5,
        )
        above = apply_phase_guardrail(
            "OPEN",
            data_quality_degraded=True,
            action="long",
            confidence=0.51,
            suggested_size_pct=0.1,
            confidence_cap_on_degraded=0.5,
        )
        assert at.adjusted_confidence == 0.5
        assert above.adjusted_confidence == 0.5


class TestFailOpen:
    def test_none_phase_fail_open(self):
        outcome = apply_phase_guardrail(
            None,  # type: ignore[arg-type]
            action="long",
            confidence=0.8,
            suggested_size_pct=0.1,
        )
        assert outcome.adjusted_action == "long"
        assert outcome.adjusted_confidence == 0.8
        assert outcome.adjusted_size_pct == 0.1
        assert "guardrail_fail_open_invalid_input" in outcome.adjustments

    def test_invalid_confidence_fail_open(self):
        outcome = apply_phase_guardrail(
            "OPEN",
            action="long",
            confidence="not-a-number",  # type: ignore[arg-type]
            suggested_size_pct=0.1,
        )
        assert outcome.adjusted_action == "long"
        assert "guardrail_fail_open_invalid_input" in outcome.adjustments

    def test_invalid_size_fail_open(self):
        outcome = apply_phase_guardrail(
            "CLOSED",
            action="long",
            confidence=0.8,
            suggested_size_pct=None,  # type: ignore[arg-type]
        )
        assert "guardrail_fail_open_invalid_input" in outcome.adjustments

    def test_unknown_phase_string_fail_open(self):
        """Garbage phase string → fail-open note, values unchanged."""
        outcome = apply_phase_guardrail(
            "\x00garbage",
            action="long",
            confidence=0.8,
            suggested_size_pct=0.1,
        )
        assert outcome.adjusted_action == "long"
        assert outcome.adjusted_confidence == 0.8

    def test_never_raises(self):
        """Guardrail must not throw for any input combination."""
        for phase in (None, 123, "", "OPEN", "CLOSED"):  # type: ignore[list-item]
            for action in ("long", None, 42):  # type: ignore[list-item]
                outcome = apply_phase_guardrail(
                    phase,  # type: ignore[arg-type]
                    action=action,  # type: ignore[arg-type]
                    confidence="x",  # type: ignore[arg-type]
                    suggested_size_pct=0.1,
                )
                assert isinstance(outcome, GuardrailOutcome)


class TestPurity:
    def test_no_mutation_of_inputs(self):
        """Immutable outcome; repeated calls identical."""
        a = apply_phase_guardrail(
            "CLOSED", action="long", confidence=0.9, suggested_size_pct=0.2
        )
        b = apply_phase_guardrail(
            "CLOSED", action="long", confidence=0.9, suggested_size_pct=0.2
        )
        assert a == b
        with pytest.raises(Exception):
            a.adjusted_action = "hold"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _build_intent integration
# ---------------------------------------------------------------------------


def _make_signal(
    action: str = "long", confidence: float = 0.9, size_pct: float = 0.15
) -> LLMSignal:
    return LLMSignal(
        action=action,  # type: ignore[arg-type]
        confidence=confidence,
        rationale="test rationale",
        suggested_size_pct=size_pct,
        risk_notes="",
    )


def _make_strategy() -> LLMSignalStrategy:
    config = LLMSignalConfig(
        llm_provider="stub",
        strategy_id="llm_signal_strategy_v1",
    )
    return LLMSignalStrategy(router=None, config=config)


def _ctx() -> dict[str, Any]:
    return {
        "symbol": "BTC/USDT",
        "equity": 1000.0,
        "current_price": 100.0,
    }


class TestBuildIntentRegression:
    def test_phase_none_guardrail_off_unchanged_behavior(self):
        """Crypto path: no phase / no guardrail → intent identical to before."""
        strategy = _make_strategy()
        signal = _make_signal(action="long", confidence=0.8, size_pct=0.1)
        intent = strategy._build_intent(signal, _ctx())

        assert intent.side == "buy"
        assert intent.quantity == pytest.approx(1000.0 * 0.1 / 100.0)
        assert intent.confidence == 0.8
        assert intent.risk_params["signal_action"] == "long"
        assert intent.risk_params["llm_confidence"] == 0.8
        assert "guardrail_adjustments" not in intent.risk_params
        assert "guardrail" not in intent.reasoning

    def test_guardrail_enabled_without_phase_is_noop(self):
        """guardrail_enabled=True but phase=None → no guardrail effect."""
        strategy = _make_strategy()
        signal = _make_signal(action="long", confidence=0.8, size_pct=0.1)
        intent = strategy._build_intent(signal, _ctx(), guardrail_enabled=True)

        assert intent.quantity == pytest.approx(1.0)
        assert "guardrail_adjustments" not in intent.risk_params

    def test_open_phase_intent_unchanged(self):
        """Explicit OPEN phase → intent identical to guardrail-off path."""
        strategy = _make_strategy()
        signal = _make_signal(action="long", confidence=0.8, size_pct=0.1)
        base = strategy._build_intent(signal, _ctx())
        guarded = strategy._build_intent(
            signal, _ctx(), phase="OPEN", guardrail_enabled=True
        )
        assert guarded.quantity == base.quantity
        assert guarded.confidence == base.confidence
        assert guarded.risk_params["signal_action"] == base.risk_params["signal_action"]
        assert "guardrail_adjustments" not in guarded.risk_params


class TestBuildIntentSuppression:
    def test_closed_phase_suppresses_to_flat_intent(self):
        strategy = _make_strategy()
        signal = _make_signal(action="long", confidence=0.9, size_pct=0.15)
        intent = strategy._build_intent(
            signal, _ctx(), phase="CLOSED", guardrail_enabled=True
        )

        assert intent.quantity == 0.0
        # Side preserved from original signal direction.
        assert intent.side == "buy"
        assert intent.risk_params["signal_action"] == "wait"
        assert intent.risk_params["suggested_size_pct"] == 0.0
        assert intent.risk_params["guardrail_adjustments"] == [
            "non_intraday_action_suppressed"
        ]
        assert "guardrail: phase=CLOSED" in intent.reasoning
        assert "non_intraday_action_suppressed" in intent.reasoning

    def test_short_signal_side_preserved_when_suppressed(self):
        strategy = _make_strategy()
        signal = _make_signal(action="short", confidence=0.9, size_pct=0.15)
        intent = strategy._build_intent(
            signal, _ctx(), phase="LUNCH_BREAK", guardrail_enabled=True
        )
        assert intent.side == "sell"
        assert intent.quantity == 0.0

    def test_open_phase_with_degraded_data_caps_confidence_in_intent(self):
        """OPEN + degraded ctx data → direction kept, confidence capped."""
        strategy = _make_strategy()
        signal = _make_signal(action="long", confidence=0.9, size_pct=0.1)
        ctx = _ctx()
        ctx["data_quality_degraded"] = True
        intent = strategy._build_intent(
            signal, ctx, phase="OPEN", guardrail_enabled=True
        )
        assert intent.quantity == pytest.approx(1.0)  # direction + size kept
        assert intent.confidence == 0.5
        assert intent.risk_params["signal_action"] == "long"
        assert intent.risk_params["guardrail_adjustments"] == [
            "confidence_capped_on_degraded_data"
        ]


# ---------------------------------------------------------------------------
# TradeIntent contract sanity
# ---------------------------------------------------------------------------


def test_guardrail_outcome_is_dataclass():
    outcome = GuardrailOutcome(
        adjusted_action="wait",
        adjusted_confidence=0.1,
        adjusted_size_pct=0.0,
        adjustments=("non_intraday_action_suppressed",),
    )
    assert outcome.adjusted_action == "wait"
    assert list(outcome.adjustments) == ["non_intraday_action_suppressed"]

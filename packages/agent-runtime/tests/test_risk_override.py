# -*- coding: utf-8 -*-
"""Focused tests for internal risk override application facts.

Ported from daily_stock_analysis (MIT License)
Copyright (c) 2026 ZhuLinsen
Source: https://github.com/ZhuLinsen/daily_stock_analysis
Adapted for SeleneQuant (AGPL-3.0-only); the upstream AgentContext helper is
replaced by explicit keyword arguments matching the rebound planner input.
"""

import pytest

from selene_space_agent.risk_override import (
    DashboardDecisionSignal,
    RiskApplicationReason,
    RiskOverrideApplication,
    RiskOverridePlan,
    build_risk_override_application,
    build_risk_override_plan,
    validate_risk_application_transition,
)


def _application(*, current_signal="buy", override_enabled=True, risk_raw=None, high_severity_flag=False):
    return build_risk_override_application(build_risk_override_plan(
        risk_evidence=risk_raw,
        high_severity_flag=high_severity_flag,
        current_signal=current_signal,
        override_enabled=override_enabled,
    ))


# --- Ported upstream behavior locks ---------------------------------------


@pytest.mark.parametrize(
    ("application", "reason"),
    [
        (_application(), RiskApplicationReason.NO_RISK_EVIDENCE),
        (_application(risk_raw={"risk_level": "high"}), RiskApplicationReason.NO_OVERRIDE_TRIGGER),
        (
            _application(risk_raw={"veto_buy": True}, override_enabled=False),
            RiskApplicationReason.OVERRIDE_DISABLED,
        ),
        (
            _application(current_signal="hold", risk_raw={"veto_buy": True}),
            RiskApplicationReason.POST_RISK_SIGNAL_ALREADY_WITHIN_RISK_LIMIT,
        ),
    ],
)
def test_non_applied_reasons_remain_distinct(application, reason):
    assert application.applied is False
    assert application.reason == reason
    assert application.from_signal is None
    assert application.to_signal is None


def test_veto_application_records_actual_transition():
    application = _application(current_signal="buy", risk_raw={"veto_buy": True})

    assert application == RiskOverrideApplication(
        evidence_present=True,
        override_enabled=True,
        trigger="risk_veto",
        applied=True,
        reason="risk_veto_applied",
        post_risk_signal="hold",
        from_signal="buy",
        to_signal="hold",
    )


def test_downgrade_application_records_actual_transition():
    application = _application(
        current_signal="hold",
        risk_raw={"signal_adjustment": "downgrade_one"},
    )

    assert application.reason == RiskApplicationReason.RISK_DOWNGRADE_APPLIED
    assert application.post_risk_signal == DashboardDecisionSignal.SELL
    assert application.from_signal == DashboardDecisionSignal.HOLD
    assert application.to_signal == DashboardDecisionSignal.SELL


# --- Selene extensions -----------------------------------------------------


def test_downgrade_two_on_buy_reaches_sell():
    application = _application(
        current_signal="buy",
        risk_raw={"signal_adjustment": "downgrade_two"},
    )

    assert application.reason == RiskApplicationReason.RISK_DOWNGRADE_APPLIED
    assert application.from_signal == DashboardDecisionSignal.BUY
    assert application.to_signal == DashboardDecisionSignal.SELL


def test_high_severity_flag_forces_veto_buy():
    plan = build_risk_override_plan(high_severity_flag=True, current_signal="buy")

    assert plan.veto_buy is True
    assert plan.reason == "high_severity_flag"
    assert plan.trigger.value == "risk_veto"

    application = build_risk_override_application(plan)
    assert application.applied is True
    assert application.reason == RiskApplicationReason.RISK_VETO_APPLIED
    assert (application.from_signal, application.to_signal) == (
        DashboardDecisionSignal.BUY,
        DashboardDecisionSignal.HOLD,
    )


def test_veto_only_applies_to_buy_signal():
    # A veto with a non-buy current signal must NOT apply — the signal is
    # already within the risk limit.
    plan = build_risk_override_plan(risk_evidence={"veto_buy": True}, current_signal="sell")

    assert plan.veto_buy is True
    assert plan.trigger.value == "risk_veto"  # veto fallback precedence
    assert plan.will_apply is False
    assert plan.target_signal == "sell"


def test_plan_without_current_signal_is_unevaluated():
    plan = build_risk_override_plan(risk_evidence={"veto_buy": True})

    assert plan.current_signal is None
    assert plan.target_signal is None
    assert plan.will_apply is None
    with pytest.raises(ValueError, match="requires an evaluated current signal"):
        build_risk_override_application(plan)


def test_signal_aliases_are_normalized():
    plan = build_risk_override_plan(risk_evidence={"veto_buy": True}, current_signal="STRONG_BUY")

    assert plan.current_signal == "buy"
    application = build_risk_override_application(plan)
    assert application.from_signal == DashboardDecisionSignal.BUY
    assert application.to_signal == DashboardDecisionSignal.HOLD


def test_downgrade_signal_clamps_at_sell_and_passes_unknown():
    from selene_space_agent.risk_override import _downgrade_signal

    assert _downgrade_signal("buy", steps=1) == "hold"
    assert _downgrade_signal("buy", steps=5) == "sell"  # clamped, no IndexError
    assert _downgrade_signal("hold", steps=1) == "sell"
    assert _downgrade_signal("sell", steps=1) == "sell"  # already most conservative
    assert _downgrade_signal("unknown_signal", steps=2) == "unknown_signal"  # passthrough
    assert _downgrade_signal("hold", steps=0) == "hold"
    assert _downgrade_signal("hold", steps=-3) == "hold"  # negative steps never upgrade


def test_trigger_property_precedence():
    base = dict(
        evidence_present=True,
        override_enabled=True,
        override_trigger_present=True,
        has_high_flag=False,
        risk_level_high=False,
        current_signal="buy",
        target_signal="hold",
        will_apply=True,
        reason="risk_veto",
    )
    # veto-buy-on-buy wins over a downgrade adjustment.
    assert RiskOverridePlan(veto_buy=True, adjustment="downgrade_one", **base).trigger \
        == "risk_veto"
    # Without buy signal, downgrade adjustment wins over veto.
    assert RiskOverridePlan(veto_buy=True, adjustment="downgrade_one", **{
        **base, "current_signal": "hold", "target_signal": "sell",
    }).trigger == "risk_downgrade"
    # Neither → veto fallback, then none.
    assert RiskOverridePlan(veto_buy=True, adjustment="", **{
        **base, "current_signal": "hold", "target_signal": "hold", "will_apply": False,
    }).trigger == "risk_veto"
    assert RiskOverridePlan(veto_buy=False, adjustment="", **{
        **base, "current_signal": "hold", "target_signal": "hold", "will_apply": False,
    }).trigger == "none"


def test_transition_validator_rejects_contradictions():
    def _reject(**kwargs):
        with pytest.raises(ValueError):
            validate_risk_application_transition(**kwargs)

    _reject(
        applied=True,
        reason=RiskApplicationReason.RISK_DOWNGRADE_APPLIED,
        post_risk_signal="hold",
        from_signal="hold",  # equal from/to
        to_signal="hold",
    )
    _reject(
        applied=True,
        reason=RiskApplicationReason.RISK_VETO_APPLIED,
        post_risk_signal="sell",  # to_signal does not match post_risk_signal
        from_signal="buy",
        to_signal="hold",
    )
    _reject(
        applied=True,
        reason=RiskApplicationReason.RISK_DOWNGRADE_APPLIED,
        post_risk_signal="buy",  # a LESS conservative target
        from_signal="hold",
        to_signal="buy",
    )
    _reject(
        applied=False,  # non-applied must not carry a transition
        reason=RiskApplicationReason.NO_OVERRIDE_TRIGGER,
        post_risk_signal="hold",
        from_signal="buy",
        to_signal="hold",
    )
    _reject(
        applied=True,  # applied but non-applied reason
        reason=RiskApplicationReason.NO_OVERRIDE_TRIGGER,
        post_risk_signal="hold",
        from_signal="buy",
        to_signal="hold",
    )


def test_to_low_sensitivity_dict_hides_raw_payload():
    plan = build_risk_override_plan(
        risk_evidence={
            "veto_buy": True,
            "secret_detail": "internal analyst note with raw payload",
        },
        current_signal="buy",
    )
    low = plan.to_low_sensitivity_dict()

    assert set(low) == {
        "evidence_present",
        "override_enabled",
        "override_trigger_present",
        "veto_buy",
        "will_apply",
        "reason",
    }
    assert "secret_detail" not in str(low)
    assert "internal analyst note" not in str(low)


def test_frozen_dataclasses_reject_mutation():
    plan = build_risk_override_plan(risk_evidence={"veto_buy": True}, current_signal="buy")
    with pytest.raises(Exception):
        plan.veto_buy = False

    application = build_risk_override_application(plan)
    with pytest.raises(Exception):
        application.applied = False

"""apply_risk_settings — saved ``risk`` settings reach the rule roster.

The bug this locks down: ``main.py`` used to assign ``rule._max_pct`` /
``rule._cooldown``, names that disappeared when ``rules.py`` was rewritten in
aa6c6697. Every assignment landed on an attribute nothing reads, so the four
limits silently stayed at their constructor defaults while the settings page
showed the user's values. ``test_rule_roster_has_every_wired_attribute`` is
the guard against that recurring.
"""

from __future__ import annotations

import pytest

from app.core.risk_assembly import _RISK_RULE_TARGETS, apply_risk_settings


class _Rule:
    """Minimal stand-in: a rule id plus whatever attributes we wire."""

    # Annotations only — no defaults, so a wire that never lands leaves the
    # attribute genuinely absent (the stale-name test relies on that).
    rule_id: str
    max_position_pct: float
    max_risk_pct: float
    max_daily_loss_pct: float
    cooldown_seconds: float

    def __init__(self, rule_id: str, **attrs: float) -> None:
        self.rule_id = rule_id
        for k, v in attrs.items():
            setattr(self, k, v)


def _roster() -> list[_Rule]:
    return [
        _Rule("max_position", max_position_pct=0.20),
        _Rule("max_single_risk", max_risk_pct=0.02),
        _Rule("daily_loss_limit", max_daily_loss_pct=0.05),
        _Rule("cooldown", cooldown_seconds=300.0),
    ]


def test_whole_percents_become_fractions() -> None:
    """Settings store "10"; the rule must see 0.10, not 10.0 (=1000%)."""
    rules = _roster()
    applied = apply_risk_settings(
        rules,
        {
            "max_position_pct": "10",
            "single_risk_pct": "2",
            "daily_loss_limit_pct": "5",
            "cooldown_seconds": "300",
        },
    )
    assert applied == 4
    assert rules[0].max_position_pct == pytest.approx(0.10)
    assert rules[1].max_risk_pct == pytest.approx(0.02)
    assert rules[2].max_daily_loss_pct == pytest.approx(0.05)
    assert rules[3].cooldown_seconds == pytest.approx(300.0)


def test_cooldown_is_not_divided() -> None:
    """Only the percent keys get /100 — seconds stay seconds."""
    rules = _roster()
    apply_risk_settings(rules, {"cooldown_seconds": "900"})
    assert rules[3].cooldown_seconds == pytest.approx(900.0)


def test_missing_keys_keep_constructor_defaults() -> None:
    """A partial settings file must not zero out limits nobody configured."""
    rules = _roster()
    applied = apply_risk_settings(rules, {"max_position_pct": "10"})
    assert applied == 1
    assert rules[1].max_risk_pct == pytest.approx(0.02)
    assert rules[2].max_daily_loss_pct == pytest.approx(0.05)
    assert rules[3].cooldown_seconds == pytest.approx(300.0)


@pytest.mark.parametrize("raw", ["", None, "abc", "nan", "inf", "-5", "101"])
def test_out_of_range_values_are_refused(raw: object) -> None:
    """Blank, non-numeric, non-finite, negative and >100% keep the default."""
    rules = _roster()
    applied = apply_risk_settings(rules, {"max_position_pct": raw})
    assert applied == 0
    assert rules[0].max_position_pct == pytest.approx(0.20)


def test_hundred_percent_is_allowed() -> None:
    """100 is the top of the documented range, not out of it."""
    rules = _roster()
    apply_risk_settings(rules, {"max_position_pct": "100"})
    assert rules[0].max_position_pct == pytest.approx(1.0)


def test_stale_attribute_is_refused_not_silently_assigned() -> None:
    """The aa6c6697 failure mode: a renamed attribute must warn, not vanish."""
    rules = [_Rule("max_position", max_pct=0.20)]  # old name
    applied = apply_risk_settings(rules, {"max_position_pct": "10"})
    assert applied == 0
    assert not hasattr(rules[0], "_max_pct")


def test_unknown_rule_id_is_skipped() -> None:
    """Rosters without a rule simply don't get that override."""
    rules = [_Rule("max_position", max_position_pct=0.20)]
    applied = apply_risk_settings(rules, {"cooldown_seconds": "900"})
    assert applied == 0
    assert rules[0].max_position_pct == pytest.approx(0.20)


def test_rule_roster_has_every_wired_attribute() -> None:
    """Guard: every (rule_id, attr) pair must exist on the real rule classes.

    This is the test the old dead-assignment code would have failed. It reads
    the actual roster from the risk engine, so a future rename there turns
    into a red test here instead of a silently ignored setting.
    """
    from selene_space_risk.rules import (
        CooldownRule,
        DailyLossLimitRule,
        MaxPositionRule,
        MaxSingleRiskRule,
    )

    classes = {
        "max_position": MaxPositionRule,
        "max_single_risk": MaxSingleRiskRule,
        "daily_loss_limit": DailyLossLimitRule,
        "cooldown": CooldownRule,
    }
    for rule_id, _key, attr, _divisor in _RISK_RULE_TARGETS:
        rule = classes[rule_id]()
        assert hasattr(rule, attr), (
            f"{classes[rule_id].__name__} has no {attr!r} — the risk settings "
            "wiring in risk_assembly._RISK_RULE_TARGETS is stale"
        )

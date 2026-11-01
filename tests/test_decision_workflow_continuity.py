"""P2-001 tests: deterministic continuity rules (pre-LLM guard).

Method basis: Al Brooks public publications (see continuity.py header) —
cleanroom originals, zero third-party corpus text.
"""

from __future__ import annotations

from selene_space_decision_workflow.continuity import (
    ContinuityGuard as CG,
    evaluate_continuity as eval_c,
    limit_order_expiry as limit_exp,
    reversal_cooldown as rev_cool,
    stop_loss_invalidation as stop_inv,
)


def _bar(low: float, high: float) -> dict:
    return {"open": high, "high": high, "low": low, "close": low, "volume": 1}


# --- reversal_cooldown -------------------------------------------------


def test_reversal_inside_cooldown_blocked() -> None:
    d = {
        "direction": "long",
        "signal_reversal": True,
        "bars_since_decision": 2,
    }
    g = rev_cool(d, [], cooldown_bars=3)
    assert not g.allowed
    assert g.rule == "reversal_cooldown"
    assert "2根" in g.reason


def test_reversal_at_cooldown_boundary_allowed() -> None:
    d = {
        "direction": "long",
        "signal_reversal": True,
        "bars_since_decision": 3,
    }
    assert rev_cool(d, [], cooldown_bars=3).allowed


def test_same_direction_signal_not_blocked() -> None:
    d = {
        "direction": "long",
        "signal_reversal": False,
        "bars_since_decision": 0,
    }
    g = rev_cool(d, [])
    assert g.allowed
    assert g.rule is None


def test_no_direction_field_cooldown_not_applicable() -> None:
    g = rev_cool({"signal_reversal": True}, [])
    assert g.allowed


# --- limit_order_expiry -------------------------------------------------


def test_stale_limit_order_expired() -> None:
    d = {"order": {"type": "limit", "bars_resting": 4}}
    g = limit_exp(d, [], expiry_bars=3)
    assert not g.allowed
    assert g.rule == "limit_order_expiry"
    assert "4根" in g.reason


def test_limit_order_within_window_alive() -> None:
    d = {"order": {"type": "limit", "bars_resting": 3}}
    assert limit_exp(d, [], expiry_bars=3).allowed


def test_market_order_not_subject_to_expiry() -> None:
    d = {"order": {"type": "market", "bars_resting": 10}}
    g = limit_exp(d, [])
    assert g.allowed
    assert g.rule is None


# --- stop_loss_invalidation ---------------------------------------------


def test_stop_touch_invalidates() -> None:
    d = {"order": {"stop_loss": 100.0}}
    bars = [_bar(99.0, 101.0)]
    g = stop_inv(d, bars)
    assert not g.allowed
    assert g.rule == "stop_loss_invalidation"
    assert "100.0" in g.reason


def test_stop_not_touched_allows() -> None:
    d = {"order": {"stop_loss": 100.0}}
    bars = [_bar(101.0, 105.0)]
    assert stop_inv(d, bars).allowed


def test_no_stop_price_not_applicable() -> None:
    g = stop_inv({"order": {}}, [_bar(1.0, 2.0)])
    assert g.allowed


# --- evaluate_continuity (composition) ----------------------------------


def test_first_blocking_rule_wins() -> None:
    d = {
        "direction": "long",
        "signal_reversal": True,
        "bars_since_decision": 1,
        "order": {"type": "limit", "bars_resting": 9},
    }
    g = eval_c(d, [_bar(50.0, 60.0)])
    # stop_loss_invalidation runs first; no stop declared → falls to cooldown.
    assert g.rule == "reversal_cooldown"


def test_stop_loss_beats_cooldown_order() -> None:
    d = {
        "direction": "long",
        "signal_reversal": True,
        "bars_since_decision": 0,
        "order": {"stop_loss": 50.0},
    }
    g = eval_c(d, [_bar(49.0, 51.0)])
    assert g.rule == "stop_loss_invalidation"


def test_all_clear_returns_allowed() -> None:
    d = {
        "direction": "long",
        "signal_reversal": False,
        "order": {"type": "market"},
    }
    g = eval_c(d, [_bar(60.0, 70.0)])
    assert isinstance(g, CG)
    assert g.allowed
    assert g.rule is None
    assert g.reason == "三规则均未否决"

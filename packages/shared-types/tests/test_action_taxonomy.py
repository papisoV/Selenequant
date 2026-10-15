"""Tests for the eight-state decision action vocabulary (action_taxonomy).

Behavior locks ported from daily_stock_analysis ``tests/test_decision_action.py``
(explicit-token and score-scale cases only — upstream's prose-parser cases are
out of scope because Selene carries no report-prose entry point), plus
Selene-original coverage for the ``action_direction`` bridge and Korean labels.
"""

from __future__ import annotations

import pytest

from selene_space_types.action_taxonomy import (
    ACTION_DECISION_TYPE,
    ACTION_DIRECTION,
    ACTION_LABELS,
    DECISION_SCALE,
    DECISION_SCALE_VERSION,
    action_decision_type,
    action_direction,
    action_for_score,
    action_label,
    decision_band_for_score,
    decision_type_for_score,
    normalize_action,
    normalize_decision_profile,
    normalize_score,
    score_action_conflicts_without_guardrail,
    score_band_metadata,
    signal_key_for_score,
)


# --- normalize_action: explicit tokens + aliases ---------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("buy", "buy"),
        ("BUY", "buy"),
        ("strong_buy", "buy"),
        ("strong buy", "buy"),
        ("add", "add"),
        ("accumulate", "add"),
        ("hold", "hold"),
        ("watch", "watch"),
        ("wait", "watch"),
        ("reduce", "reduce"),
        ("trim", "reduce"),
        ("sell", "sell"),
        ("strong_sell", "sell"),
        ("strong sell", "sell"),
        ("avoid", "avoid"),
        ("alert", "alert"),
        ("  sell  ", "sell"),
        ("add", "add"),
    ],
)
def test_normalize_action_recognizes_explicit_tokens_and_aliases(
    value: str, expected: str
) -> None:
    assert normalize_action(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "观察",
        "等待突破后买入",
        "买入或卖出",
        "buy or sell",
        "不建议买入",
        "no buy",
        "hold on",
        "buyback",
    ],
)
def test_normalize_action_returns_none_for_unknown_or_ambiguous(value: object) -> None:
    """Prose / negated / unknown text is NOT parsed — it returns None."""
    assert normalize_action(value) is None


# --- action_label: localization --------------------------------------------


def test_action_label_supports_zh_en_and_ko() -> None:
    assert action_label("avoid", "zh") == "回避"
    assert action_label("avoid", "en") == "Avoid"
    assert action_label("avoid", "ko") == "회피"


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("buy", "매수"),
        ("add", "추가 매수"),
        ("hold", "보유"),
        ("reduce", "비중축소"),
        ("sell", "매도"),
        ("watch", "관망"),
        ("avoid", "회피"),
        ("alert", "경고"),
    ],
)
def test_action_label_korean_vocabulary(action: str, expected: str) -> None:
    assert action_label(action, "ko") == expected


def test_action_label_unknown_language_falls_back_to_english() -> None:
    assert action_label("buy", "fr") == "Buy"
    assert action_label("buy", None) == "Buy"


def test_action_label_returns_none_for_unknown_action() -> None:
    assert action_label("not-an-action", "en") is None


# --- three-state rollup + Selene direction bridge --------------------------


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("buy", "buy"),
        ("add", "buy"),
        ("hold", "hold"),
        ("watch", "hold"),
        ("avoid", "hold"),
        ("alert", "hold"),
        ("reduce", "sell"),
        ("sell", "sell"),
    ],
)
def test_action_decision_type_three_state_rollup(action: str, expected: str) -> None:
    assert action_decision_type(action) == expected


def test_action_decision_type_unknown_returns_none() -> None:
    assert action_decision_type("nope") is None


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("buy", "long"),
        ("add", "long"),
        ("reduce", "short"),
        ("sell", "short"),
        ("hold", "neutral"),
        ("watch", "neutral"),
        ("avoid", "neutral"),
        ("alert", "neutral"),
    ],
)
def test_action_direction_bridge_to_selene_execution_vocabulary(
    action: str, expected: str
) -> None:
    assert action_direction(action) == expected


def test_action_direction_unknown_returns_none() -> None:
    assert action_direction("nope") is None


# --- score scale -----------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "signal", "action", "decision_type"),
    [
        (0, "sell", "sell", "sell"),
        (19, "sell", "sell", "sell"),
        (20, "reduce", "reduce", "sell"),
        (39, "reduce", "reduce", "sell"),
        (40, "watch", "watch", "hold"),
        (59, "watch", "watch", "hold"),
        (60, "buy", "buy", "buy"),
        (79, "buy", "buy", "buy"),
        (80, "strong_buy", "buy", "buy"),
        (100, "strong_buy", "buy", "buy"),
    ],
)
def test_score_scale_bands(
    score: int, signal: str, action: str, decision_type: str
) -> None:
    assert signal_key_for_score(score) == signal
    assert action_for_score(score) == action
    assert decision_type_for_score(score) == decision_type


def test_canonical_decision_scale_boundaries_from_upstream() -> None:
    """Exact upstream boundary cases (test_decision_action.py)."""
    assert (
        signal_key_for_score(28),
        action_for_score(28),
        decision_type_for_score(28),
    ) == (
        "reduce",
        "reduce",
        "sell",
    )
    assert (
        signal_key_for_score(42),
        action_for_score(42),
        decision_type_for_score(42),
    ) == (
        "watch",
        "watch",
        "hold",
    )
    assert (
        signal_key_for_score(72),
        action_for_score(72),
        decision_type_for_score(72),
    ) == (
        "buy",
        "buy",
        "buy",
    )


@pytest.mark.parametrize("value", [None, "", "abc", -1, 101])
def test_normalize_score_rejects_out_of_range_and_non_numeric(value: object) -> None:
    assert normalize_score(value) is None


def test_normalize_score_accepts_numeric_strings_and_floats() -> None:
    assert normalize_score("72") == 72
    assert normalize_score(72.9) == 72
    assert normalize_score(72.2) == 72


def test_score_band_metadata_is_stable_and_complete() -> None:
    meta = score_band_metadata(72)
    assert meta == {
        "scale_version": DECISION_SCALE_VERSION,
        "score": 72,
        "score_band": "60-79",
        "signal_key": "buy",
        "canonical_action": "buy",
        "canonical_decision_type": "buy",
    }


def test_score_band_metadata_empty_for_invalid_score() -> None:
    assert score_band_metadata(None) == {}
    assert score_band_metadata(200) == {}


def test_decision_band_for_score_returns_band_object() -> None:
    band = decision_band_for_score(85)
    assert band is not None
    assert band.signal_key == "strong_buy"
    assert band.min_score == 80 and band.max_score == 100


# --- guardrail consistency -------------------------------------------------


def test_score_action_conflict_flags_neutral_action_against_directional_score() -> None:
    assert score_action_conflicts_without_guardrail(score=72, action="hold") is True
    assert score_action_conflicts_without_guardrail(score=28, action="watch") is True


def test_score_action_conflict_cleared_by_explicit_guardrail() -> None:
    assert (
        score_action_conflicts_without_guardrail(
            score=72, action="hold", guardrail_reason="等待回踩确认"
        )
        is False
    )


def test_score_action_conflict_ignores_directional_actions() -> None:
    assert score_action_conflicts_without_guardrail(score=72, action="buy") is False
    assert score_action_conflicts_without_guardrail(score=28, action="reduce") is False


def test_score_action_conflict_ignores_mid_band_score() -> None:
    """Score 40-59 maps to watch, so a watch action is consistent."""
    assert score_action_conflicts_without_guardrail(score=50, action="watch") is False


# --- decision profile ------------------------------------------------------


def test_normalize_decision_profile_accepts_valid_values() -> None:
    assert normalize_decision_profile("conservative") == "conservative"
    assert normalize_decision_profile("BALANCED") == "balanced"
    assert normalize_decision_profile("  aggressive  ") == "aggressive"


def test_normalize_decision_profile_passes_through_unknown() -> None:
    assert normalize_decision_profile(None) is None
    assert normalize_decision_profile("") is None


def test_normalize_decision_profile_raises_on_invalid() -> None:
    with pytest.raises(ValueError, match="decision_profile must be one of"):
        normalize_decision_profile("reckless")


def test_normalize_decision_profile_field_name_in_error() -> None:
    with pytest.raises(ValueError, match="profile must be one of"):
        normalize_decision_profile("bad", field_name="profile")


# --- structural invariants -------------------------------------------------


def test_every_action_has_a_label_row_in_all_languages() -> None:
    for action in ACTION_LABELS:
        for lang in ("zh", "en", "ko"):
            assert ACTION_LABELS[action][lang]


def test_action_lookup_tables_cover_the_full_eight_state_vocabulary() -> None:
    eight = {"buy", "add", "hold", "reduce", "sell", "watch", "avoid", "alert"}
    assert set(ACTION_LABELS) == eight
    assert set(ACTION_DECISION_TYPE) == eight
    assert set(ACTION_DIRECTION) == eight


def test_decision_scale_bands_are_contiguous_and_span_zero_to_hundred() -> None:
    ordered = sorted(DECISION_SCALE, key=lambda b: b.min_score)
    assert ordered[0].min_score == 0
    assert ordered[-1].max_score == 100
    for lower, upper in zip(ordered, ordered[1:]):
        assert upper.min_score == lower.max_score + 1

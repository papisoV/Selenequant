"""Behaviour locks for signal-signal forward outcome evaluation.

These port the *semantics* — not the fixtures — of DSA's
``tests/test_decision_signal_outcome_service.py`` against the pure functions in
:mod:`selene_space_backtest.signal_outcome`. The load-bearing cases are the
ones where a naive port silently mis-scores:

  - ``not_up`` (reduce/sell/avoid) must use the defensive branch, never the
    ``not_down``/``flat`` one — a successful sell call (price fell) is a HIT.
  - watch/alert are non-directional and must be ``unable`` without ever
    touching price data.
  - a window that has not filled is ``unable``, never a completed verdict.
"""

from __future__ import annotations

import pytest

from selene_space_backtest.signal_outcome import (
    DEFAULT_NEUTRAL_BAND_PCT,
    EVAL_COMPLETED,
    EVAL_UNABLE,
    RETRYABLE_UNABLE_REASONS,
    SIGNAL_OUTCOME_ENGINE_VERSION,
    SIGNAL_OUTCOME_HORIZONS,
    classify_signal_outcome,
    direction_for_action,
    evaluate_signal,
)


def _bars(*closes: float) -> list[dict[str, float]]:
    """Ascending bars with close=high=low (return alone drives the verdict)."""
    return [
        {"close": c, "high": c, "low": c} for c in closes
    ]


# --- direction_for_action ---------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("buy", "up"),
        ("add", "up"),
        ("hold", "not_down"),
        ("reduce", "not_up"),
        ("sell", "not_up"),
        ("avoid", "not_up"),
        ("watch", None),
        ("alert", None),
    ],
)
def test_direction_for_action(action: str, expected: str | None) -> None:
    assert direction_for_action(action) == expected


@pytest.mark.parametrize("value", [None, "", "  ", "BUY ", "nonsense", 42])
def test_direction_for_action_tolerates_and_rejects(value: object) -> None:
    if isinstance(value, str) and value.strip().lower() in {"buy"}:
        assert direction_for_action(value) == "up"
    else:
        assert direction_for_action(value) is None


def test_direction_buy_is_case_and_space_insensitive() -> None:
    assert direction_for_action("  BUY ") == "up"


# --- classify_signal_outcome: the not_up asymmetry --------------------------


def test_not_up_scores_a_fall_as_a_hit_not_a_loss() -> None:
    # The exact defect this module exists to avoid: price -5% on a sell call is
    # a successful defensive call. A flat-branch fallthrough would call it loss.
    outcome, correct = classify_signal_outcome(
        stock_return_pct=-5.0, direction_expected="not_up",
        neutral_band_pct=2.0,
    )
    assert (outcome, correct) == ("hit", True)


def test_not_up_hits_anywhere_at_or_below_the_band() -> None:
    for r in (-10.0, -2.0, 0.0, 2.0):
        outcome, correct = classify_signal_outcome(
            stock_return_pct=r, direction_expected="not_up",
            neutral_band_pct=2.0,
        )
        assert (outcome, correct) == ("hit", True), f"r={r}"


def test_not_up_misses_only_above_the_band_and_has_no_neutral() -> None:
    outcome, correct = classify_signal_outcome(
        stock_return_pct=2.01, direction_expected="not_up",
        neutral_band_pct=2.0,
    )
    assert (outcome, correct) == ("miss", False)


def test_not_up_and_not_down_disagree_on_a_rise() -> None:
    # A rise is a win for a "not down" call (hold) and a miss for "not up" (sell).
    up = classify_signal_outcome(
        stock_return_pct=5.0, direction_expected="not_down",
        neutral_band_pct=2.0,
    )
    defensive = classify_signal_outcome(
        stock_return_pct=5.0, direction_expected="not_up",
        neutral_band_pct=2.0,
    )
    assert up == ("hit", True)
    assert defensive == ("miss", False)


# --- classify_signal_outcome: up / not_down neutral bands -------------------


@pytest.mark.parametrize(
    ("r", "expected"),
    [
        (5.0, ("hit", True)),
        (-5.0, ("miss", False)),
        (1.0, ("neutral", None)),
        (-1.0, ("neutral", None)),
        (2.0, ("hit", True)),  # band edge is inclusive
        (-2.0, ("miss", False)),
    ],
)
def test_up_direction_bands(r: float, expected: tuple[str, bool | None]) -> None:
    assert classify_signal_outcome(
        stock_return_pct=r, direction_expected="up", neutral_band_pct=2.0
    ) == expected


@pytest.mark.parametrize(
    ("r", "expected"),
    [
        (0.0, ("hit", True)),
        (10.0, ("hit", True)),
        (-5.0, ("miss", False)),
        (-1.0, ("neutral", None)),
    ],
)
def test_not_down_direction_bands(r: float, expected: tuple[str, bool | None]) -> None:
    assert classify_signal_outcome(
        stock_return_pct=r, direction_expected="not_down", neutral_band_pct=2.0
    ) == expected


def test_classify_none_return_is_undecided() -> None:
    assert classify_signal_outcome(
        stock_return_pct=None, direction_expected="up", neutral_band_pct=2.0
    ) == (None, None)


def test_classify_unknown_direction_is_undecided() -> None:
    # A token no branch recognises must NOT be silently scored as flat.
    assert classify_signal_outcome(
        stock_return_pct=5.0, direction_expected="sideways", neutral_band_pct=2.0
    ) == (None, None)


def test_neutral_band_uses_magnitude() -> None:
    # A negative band is treated as its magnitude, never as an inverted range.
    assert classify_signal_outcome(
        stock_return_pct=2.0, direction_expected="up", neutral_band_pct=-2.0
    ) == ("hit", True)


# --- evaluate_signal: gates -------------------------------------------------


def test_watch_and_alert_are_unable_without_touching_price_data() -> None:
    class ExplodingBars:
        def __bool__(self) -> bool:  # pragma: no cover - must not be reached
            raise AssertionError("non-directional signal must not read bars")

        def __iter__(self):  # pragma: no cover - must not be reached
            raise AssertionError("non-directional signal must not read bars")

    for action in ("watch", "alert"):
        result = evaluate_signal(
            action=action, horizon="3d", start_price=100.0,
            forward_bars=ExplodingBars(),  # type: ignore[arg-type]
        )
        assert result["eval_status"] == EVAL_UNABLE
        assert result["unable_reason"] == "non_directional_action"
        assert "outcome" not in result


def test_unsupported_horizon_is_unable() -> None:
    result = evaluate_signal(
        action="buy", horizon="intraday", start_price=100.0, forward_bars=_bars(101, 102, 103)
    )
    assert result["eval_status"] == EVAL_UNABLE
    assert result["unable_reason"] == "unsupported_horizon"
    # direction is still reported when it is known, even on an unable verdict
    assert result["direction_expected"] == "up"


@pytest.mark.parametrize("bad", [None, 0, -1.0, "x"])
def test_missing_or_invalid_anchor_price_is_retryable_unable(bad: object) -> None:
    result = evaluate_signal(
        action="buy", horizon="3d", start_price=bad, forward_bars=_bars(101, 102, 103)
    )
    assert result["eval_status"] == EVAL_UNABLE
    assert result["unable_reason"] in RETRYABLE_UNABLE_REASONS
    assert "outcome" not in result


def test_insufficient_forward_bars_is_retryable_unable() -> None:
    # Only 2 bars for a 3d window — must NOT be scored.
    result = evaluate_signal(
        action="buy", horizon="3d", start_price=100.0, forward_bars=_bars(101, 102)
    )
    assert result["eval_status"] == EVAL_UNABLE
    assert result["unable_reason"] == "insufficient_forward_bars"
    assert result["bars_available"] == 2
    assert "outcome" not in result


def test_invalid_end_close_is_retryable_unable() -> None:
    result = evaluate_signal(
        action="buy", horizon="3d", start_price=100.0,
        forward_bars=[{"close": 101.0}, {"close": 102.0}, {"close": None}],
    )
    assert result["eval_status"] == EVAL_UNABLE
    assert result["unable_reason"] == "invalid_end_close"


def test_retry_after_data_arrives_completes() -> None:
    # Same signal, first too few bars (unable), then enough (completed).
    too_short = evaluate_signal(
        action="buy", horizon="3d", start_price=100.0, forward_bars=_bars(101, 102)
    )
    assert too_short["eval_status"] == EVAL_UNABLE

    enough = evaluate_signal(
        action="buy", horizon="3d", start_price=100.0, forward_bars=_bars(103, 104, 105)
    )
    assert enough["eval_status"] == EVAL_COMPLETED
    assert enough["stock_return_pct"] == 5.0
    assert enough["outcome"] == "hit"


# --- evaluate_signal: completed verdicts ------------------------------------


def test_sell_signal_completes_with_correct_semantics() -> None:
    result = evaluate_signal(
        action="reduce", horizon="3d", start_price=100.0, forward_bars=_bars(99, 98, 97)
    )
    assert result["eval_status"] == EVAL_COMPLETED
    assert result["direction_expected"] == "not_up"
    assert result["outcome"] == "hit"
    assert result["direction_correct"] is True


def test_window_takes_only_the_first_n_bars() -> None:
    # 5 bars supplied for a 3d window: the 4th/5th must be ignored.
    result = evaluate_signal(
        action="buy", horizon="3d", start_price=100.0,
        forward_bars=_bars(101, 102, 103, 999, 999),
    )
    assert result["stock_return_pct"] == 3.0


def test_engine_version_and_band_recorded() -> None:
    result = evaluate_signal(
        action="buy", horizon="1d", start_price=100.0, forward_bars=_bars(101)
    )
    assert result["engine_version"] == SIGNAL_OUTCOME_ENGINE_VERSION
    assert result["neutral_band_pct"] == DEFAULT_NEUTRAL_BAND_PCT
    assert result["eval_window_days"] == 1


@pytest.mark.parametrize("horizon", sorted(SIGNAL_OUTCOME_HORIZONS))
def test_every_supported_horizon_evaluates(horizon: str) -> None:
    days = SIGNAL_OUTCOME_HORIZONS[horizon]
    result = evaluate_signal(
        action="buy", horizon=horizon, start_price=100.0,
        forward_bars=_bars(*([101.0] * days)),
    )
    assert result["eval_status"] == EVAL_COMPLETED
    assert result["eval_window_days"] == days


def test_max_high_min_low_tracked_over_window() -> None:
    bars = [
        {"close": 101.0, "high": 105.0, "low": 99.0},
        {"close": 102.0, "high": 110.0, "low": 98.0},
        {"close": 103.0, "high": 104.0, "low": 100.0},
    ]
    result = evaluate_signal(action="buy", horizon="3d", start_price=100.0, forward_bars=bars)
    assert result["max_high"] == 110.0
    assert result["min_low"] == 98.0

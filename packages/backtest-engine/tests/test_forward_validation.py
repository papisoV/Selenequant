"""Behavior lock for the forward-validation pure functions.

Pinned invariants, each traceable to a design decision:
- A record with no derivable direction is UNJUDGEABLE (insufficient), never
  silently treated as neutral or flat.
- A forward window with fewer bars than the window size never evaluates to
  ``completed`` -- partial data must not produce a verdict.
- The neutral band: a return inside the band is "neutral" with correctness
  None (unjudged), distinct from a wrong verdict (False).
- ``window_end_of`` only speaks for completed evaluations and returns the
  last bar date exactly.
"""

from __future__ import annotations

from typing import Any

from selene_space_backtest.forward_validation import (
    EVAL_COMPLETED,
    EVAL_ERROR,
    EVAL_INSUFFICIENT,
    analysis_bar_date,
    analysis_close,
    classify_outcome,
    evaluate_forward,
    expected_direction,
    window_end_of,
)

_ANCHOR_TS_MS = 1_756_684_800_000  # 2025-09-01T00:00:00Z
_DAY_MS = 86_400_000


def _record(
    *,
    decision: str = "market",
    side: str | None = "long",
    closes: list[float] | None = None,
) -> dict[str, Any]:
    """Minimal structured analysis record (v009 record_json shape)."""
    kline_closes = closes if closes is not None else [100.0, 101.0, 102.0]
    klines = [
        {
            "timestamp": _ANCHOR_TS_MS - (len(kline_closes) - 1 - i) * _DAY_MS,
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": 1.0,
        }
        for i, c in enumerate(kline_closes)
    ]
    stage2: dict[str, Any] = {"decision": decision}
    if side is not None:
        stage2["trade_setup"] = {"direction": side}
    return {
        "meta": {"input_id": "in-1", "timestamp": "2025-09-01T00:00:00+00:00"},
        "input_data": {"symbol": "000001.SZ", "interval": "1d", "klines": klines},
        "stage1": {"market_regime": "trend_up", "entry_gate": "proceed"},
        "stage2": stage2,
    }


def _forward(closes: list[float], *, first_day: int = 2) -> list[dict[str, Any]]:
    """Forward daily bars, ascending, starting 2025-09-{first_day}."""
    return [
        {
            "date": f"2025-09-{first_day + i:02d}",
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": 1.0,
        }
        for i, c in enumerate(closes)
    ]


# --- direction derivation ---------------------------------------------------


def test_no_trade_is_flat_even_without_setup():
    assert expected_direction(_record(decision="no_trade", side=None)) == "flat"


def test_long_entry_is_up():
    assert expected_direction(_record(decision="limit", side="long")) == "up"


def test_short_entry_is_down():
    assert expected_direction(_record(decision="breakout", side="short")) == "down"


def test_market_decision_with_long_side_is_up():
    assert expected_direction(_record(decision="market", side="long")) == "up"


def test_unknown_decision_cannot_be_mapped():
    assert expected_direction(_record(decision="weird", side="long")) is None


def test_entry_decision_without_setup_cannot_be_mapped():
    assert expected_direction(_record(decision="market", side=None)) is None


def test_missing_stage2_cannot_be_mapped():
    rec = _record()
    rec["stage2"] = None
    assert expected_direction(rec) is None


# --- neutral-band classification ---------------------------------------------


def test_up_expected_win_when_return_at_or_above_band():
    assert classify_outcome(
        stock_return_pct=5.0, direction_expected="up", neutral_band_pct=2.0
    ) == ("win", True)


def test_up_expected_loss_when_return_at_or_below_negative_band():
    assert classify_outcome(
        stock_return_pct=-5.0, direction_expected="up", neutral_band_pct=2.0
    ) == ("loss", False)


def test_up_expected_neutral_inside_band_is_unjudged_not_wrong():
    outcome, correct = classify_outcome(
        stock_return_pct=1.0, direction_expected="up", neutral_band_pct=2.0
    )
    assert outcome == "neutral"
    assert correct is None


def test_down_expected_win_when_return_drops_past_band():
    assert classify_outcome(
        stock_return_pct=-3.0, direction_expected="down", neutral_band_pct=2.0
    ) == ("win", True)


def test_down_expected_loss_when_return_rises_past_band():
    assert classify_outcome(
        stock_return_pct=3.0, direction_expected="down", neutral_band_pct=2.0
    ) == ("loss", False)


def test_flat_expected_win_inside_band():
    assert classify_outcome(
        stock_return_pct=1.5, direction_expected="flat", neutral_band_pct=2.0
    ) == ("win", True)


def test_flat_expected_loss_outside_band_on_either_side():
    assert classify_outcome(
        stock_return_pct=3.0, direction_expected="flat", neutral_band_pct=2.0
    ) == ("loss", False)
    assert classify_outcome(
        stock_return_pct=-3.0, direction_expected="flat", neutral_band_pct=2.0
    ) == ("loss", False)


def test_not_down_expected_win_at_zero():
    assert classify_outcome(
        stock_return_pct=0.0, direction_expected="not_down", neutral_band_pct=2.0
    ) == ("win", True)


def test_none_return_is_unjudgeable():
    assert classify_outcome(
        stock_return_pct=None, direction_expected="up", neutral_band_pct=2.0
    ) == (None, None)


# --- evaluate_forward ---------------------------------------------------------


def test_short_bar_window_is_insufficient_never_completed():
    rec = _record(closes=[100.0])
    out = evaluate_forward(rec, _forward([101.0, 102.0, 103.0]), eval_window_days=10)
    assert out["eval_status"] == EVAL_INSUFFICIENT
    assert out["reason"] == "forward_bars_shorter_than_window"
    assert out["bars_available"] == 3


def test_completed_happy_path_long_win():
    rec = _record(decision="market", side="long", closes=[100.0])
    out = evaluate_forward(rec, _forward([100.0] * 9 + [110.0]))
    assert out["eval_status"] == EVAL_COMPLETED
    assert out["direction_expected"] == "up"
    assert out["outcome"] == "win"
    assert out["direction_correct"] is True
    assert out["start_price"] == 100.0
    assert out["end_close"] == 110.0
    assert out["stock_return_pct"] == 10.0
    assert len(out["bar_window"]) == 10


def test_highs_and_lows_captured_from_window():
    rec = _record(closes=[100.0])
    bars = _forward([100.0] * 10)
    bars[3]["high"] = 112.0
    bars[5]["low"] = 96.0
    out = evaluate_forward(rec, bars)
    assert out["max_high"] == 112.0
    assert out["min_low"] == 96.0


def test_undirected_record_is_insufficient_not_neutral():
    rec = _record(decision="weird", side="long")
    out = evaluate_forward(rec, _forward([110.0] * 10))
    assert out["eval_status"] == EVAL_INSUFFICIENT
    assert out["reason"] == "no_direction_in_record"


def test_non_positive_window_is_error():
    out = evaluate_forward(_record(), _forward([110.0] * 10), eval_window_days=0)
    assert out["eval_status"] == EVAL_ERROR


def test_invalid_start_price_is_error():
    rec = _record(closes=[0.0])
    out = evaluate_forward(rec, _forward([110.0] * 10))
    assert out["eval_status"] == EVAL_ERROR


def test_no_trade_flat_inside_band_is_a_correct_call():
    rec = _record(decision="no_trade", side=None, closes=[100.0])
    out = evaluate_forward(rec, _forward([100.0] * 9 + [101.0]))
    assert out["eval_status"] == EVAL_COMPLETED
    assert out["outcome"] == "win"
    assert out["direction_correct"] is True


# --- idempotency key + anchor -------------------------------------------------


def test_window_end_is_last_forward_bar_date():
    rec = _record(closes=[100.0])
    out = evaluate_forward(rec, _forward([110.0] * 10))
    assert window_end_of(out) == "2025-09-11"


def test_window_end_none_for_incomplete_evaluation():
    out = evaluate_forward(_record(), _forward([1.0]))
    assert window_end_of(out) is None


def test_anchor_price_and_date_from_last_kline():
    rec = _record(closes=[100.0, 105.0])
    assert analysis_close(rec) == 105.0
    assert analysis_bar_date(rec) == "2025-09-01"


def test_anchor_helpers_tolerate_malformed_records():
    assert analysis_close({}) is None
    assert analysis_bar_date({}) is None
    bad = _record(closes=[100.0])
    bad["input_data"]["klines"][-1]["timestamp"] = "not-a-number"
    assert analysis_bar_date(bad) is None

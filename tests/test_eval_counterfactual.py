"""Pure-function tests for the counterfactual next_period scorer (B2-a).

Covers the acceptance criteria: records without next_period are out of the
population (None, not zero); the persisted Stage-1 path is what real records
carry (4/7 measured 2026-09-17, including gate short-circuits); thin bar
coverage yields unsettled verdicts with pnl None (never a fabricated 0);
directional hits reproduce hand-computed forward returns; neutral counts but
never hits; unknown direction strings are reported unscorable, not guessed.

Fixtures mirror the real persisted shape: next_period lives inside
``stage1.diagnosis_or_decision`` (the diagnosis_summary the orchestrator
persists), and the anchor is ``input_data.summary.last_close``.
"""

from __future__ import annotations

import pytest

from selene_space_decision_workflow.eval_counterfactual import (
    direction_of,
    forward_close,
    next_period_of,
    score_record,
    summarize,
)


def _bar(ts: int, close: float) -> dict:
    return {
        "ts": ts,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 0.0,
    }


def _record(direction="long", *, last_close=100.0, gate="wait", nested=True):
    """A DW record in the REAL persisted shape (StageRecord dump).

    ``next_period`` sits inside ``stage1.diagnosis_or_decision`` — the
    diagnosis_summary the orchestrator persists (orchestrator.py:143). The
    measured real records are gate short-circuits at the Stage-2 level while
    Stage 1 still carries next_period, so ``stage2`` here is the flat
    short-circuit body.
    """
    dod = {"state": "trend_up", "gate_result": gate}
    if nested and direction is not None:
        dod["next_period"] = {
            "direction": direction,
            "watch_levels": [last_close - 1.0, last_close + 1.0],
        }
    return {
        "meta": {"timestamp": "2026-09-17T00:00:00+00:00"},
        "input_data": {"summary": {"last_close": last_close, "interval": "1h"}},
        "stage1": {
            "messages": [],
            "response": {"gate_result": gate},
            "trace": [],
            "diagnosis_or_decision": dod,
        },
        "stage2": {
            "decision": "wait",
            "decision_summary": {"short_circuited": True, "gate": gate},
        },
    }


def _bars(start_ts: int, step_ms: int, closes: list[float]) -> list[dict]:
    return [_bar(start_ts + i * step_ms, c) for i, c in enumerate(closes)]


# --- population ---------------------------------------------------------------


def test_record_without_next_period_is_out_of_population():
    assert score_record(_record(direction=None), []) is None
    assert next_period_of({"stage1": {"diagnosis_or_decision": {}}}) is None
    # non-dict / missing stage1 must not raise
    assert next_period_of({"stage1": "x"}) is None
    assert next_period_of({}) is None


def test_short_circuit_records_still_carry_next_period():
    """The whole point: gate-blocked records are scoreable (measured 4/7)."""
    rec = _record("long", gate="wait")
    assert next_period_of(rec) is not None
    v = score_record(rec, _bars(0, 3_600_000, [101.0] * 24), horizon=24)
    assert v is not None and v["settled"] is True and v["hit"] is True


def test_stage1_wins_when_both_stages_carry_next_period():
    rec = _record("long")
    rec["stage2"]["decision_summary"]["next_period"] = {"direction": "down"}
    assert next_period_of(rec)["direction"] == "long"


# --- direction vocabulary -----------------------------------------------------


@pytest.mark.parametrize(
    "raw,normalised",
    [
        ("up", "up"),
        ("long", "long"),
        ("down", "down"),
        ("short", "short"),
        ("neutral", "neutral"),
        ("UP", "up"),
        ("Long", "long"),
        ("sideways", None),
        (None, None),
        (42, None),
    ],
)
def test_direction_normalisation(raw, normalised):
    np_ = {} if raw is None else {"direction": raw}
    assert direction_of(np_) == normalised


def test_unknown_direction_is_unscorable_not_guessed():
    rec = _record()
    rec["stage1"]["diagnosis_or_decision"]["next_period"] = {"direction": "sideways"}
    v = score_record(rec, _bars(0, 3_600_000, [200.0] * 24), horizon=24)
    assert v["unscorable"] is True and v["hit"] is None and v["pnl_pct"] is None


# --- forward bars / honesty ---------------------------------------------------


def test_thin_bars_yield_unsettled_with_pnl_none():
    rec = _record("long")
    v = score_record(rec, _bars(0, 3_600_000, [101.0] * 5), horizon=24)
    assert v["settled"] is False and v["pnl_pct"] is None and v["hit"] is None
    assert v["direction"] == "long"


def test_forward_close_never_fabricates():
    bars = _bars(0, 3_600_000, [100.0, 101.0])
    assert forward_close(bars, horizon=2) == 101.0
    assert forward_close(bars, horizon=3) is None
    assert forward_close(bars, horizon=0) is None
    assert forward_close([{"ts": 0, "close": None}], horizon=1) is None


def test_anchor_missing_yields_unsettled():
    rec = _record("long")
    rec["input_data"]["summary"]["last_close"] = None
    v = score_record(rec, _bars(0, 3_600_000, [101.0] * 24), horizon=24)
    assert v["settled"] is False and v["hit"] is None


# --- hand-computed scoring ----------------------------------------------------


def test_bullish_hit_hand_computed():
    rec = _record("long", last_close=100.0)
    bars = _bars(0, 3_600_000, [99.0] + [100.5] * 23)  # settles at bar 24: 100.5
    v = score_record(rec, bars, horizon=24)
    assert v["settled"] and v["hit"] is True
    assert v["pnl_pct"] == pytest.approx(0.005)


def test_bullish_miss_when_price_falls():
    rec = _record("long", last_close=100.0)
    bars = _bars(0, 3_600_000, [98.0] * 24)
    v = score_record(rec, bars, horizon=24)
    assert v["hit"] is False
    assert v["pnl_pct"] == pytest.approx(-0.02)


def test_bearish_hit_is_inverted_sign():
    rec = _record("short", last_close=100.0)
    bars = _bars(0, 3_600_000, [97.5] * 24)
    v = score_record(rec, bars, horizon=24)
    assert v["hit"] is True
    assert v["pnl_pct"] == pytest.approx(-0.025)


def test_neutral_is_counted_never_hit():
    rec = _record("neutral", last_close=100.0)
    bars = _bars(0, 3_600_000, [110.0] * 24)
    v = score_record(rec, bars, horizon=24)
    assert v["settled"] is True
    assert v["hit"] is None  # not a hit, not a miss
    assert v["pnl_pct"] == pytest.approx(0.10)  # move still reported


def test_flat_move_exact_anchor_is_a_miss_for_bullish():
    """pnl == 0 is not > 0: a long that goes nowhere did not hit."""
    rec = _record("long", last_close=100.0)
    bars = _bars(0, 3_600_000, [100.0] * 24)
    assert score_record(rec, bars, horizon=24)["hit"] is False


# --- aggregation --------------------------------------------------------------


def test_summarize_hand_computed():
    verdicts = [
        score_record(
            _record("long", last_close=100.0),
            _bars(0, 3_600_000, [102.0] * 24),
            horizon=24,
        ),  # hit, +2%
        score_record(
            _record("long", last_close=100.0),
            _bars(0, 3_600_000, [99.0] * 24),
            horizon=24,
        ),  # miss, -1%
        score_record(
            _record("short", last_close=50.0),
            _bars(0, 3_600_000, [49.0] * 24),
            horizon=24,
        ),  # hit, -2%
        score_record(
            _record("neutral", last_close=10.0),
            _bars(0, 3_600_000, [11.0] * 24),
            horizon=24,
        ),  # neutral
        score_record(
            _record("long", last_close=7.0), _bars(0, 3_600_000, [7.0] * 3), horizon=24
        ),  # unsettled
        score_record({"stage2": {"decision": "wait"}}, []),  # out of pop
    ]
    s = summarize([v for v in verdicts if v is not None])
    assert s["population"] == 5
    assert s["settled"] == 4
    assert s["unsettled"] == 1
    assert s["directional_settled"] == 3
    assert s["neutral_settled"] == 1
    assert s["hit_rate"] == pytest.approx(2 / 3)
    assert s["mean_pnl_pct_directional"] == pytest.approx((0.02 - 0.01 - 0.02) / 3)
    assert s["mean_pnl_pct_neutral"] == pytest.approx(0.10)


def test_summarize_empty():
    s = summarize([])
    assert s["population"] == 0 and s["hit_rate"] is None

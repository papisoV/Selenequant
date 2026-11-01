"""Known-answer tests for pa_stats_lib over synthetic bars.

Each test constructs bars whose expected statistics are computable by
hand — no real data, no randomness (zero-mock discipline: these verify
the *logic*, the experiments script verifies the *data pipeline*).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pa_research.pa_stats_lib import (  # noqa: E402
    BreakoutEvent,
    SpikeEvent,
    SwingPoint,
    atr_series,
    breakout_events,
    direction_edge,
    mm_hit,
    spike_counter,
    spike_reversal_rate,
    survival_count,
    swing_detect,
    true_range,
    wilson_ci,
)


def _flat(n: int) -> tuple[list[float], list[float], list[float]]:
    """n bars of a flat series (o=h=l=c=10)."""
    return [10.0] * n, [10.0] * n, [10.0] * n


# ---------------------------------------------------------------- swing


def test_swing_detect_high_basic() -> None:
    # flat 10s, one peak of 12 at index 3, k=2
    highs = [10, 10, 10, 12, 10, 10, 10]
    lows = [9, 9, 9, 11, 9, 9, 9]
    swings = swing_detect(highs, lows, k=2)
    assert swings == [SwingPoint(3, 12.0, "high")]


def test_swing_detect_low_and_multiple() -> None:
    # V at 2 then peak at 6
    highs = [10, 10, 10, 10, 10, 12, 10]
    lows = [10, 10, 8, 10, 10, 10, 10]
    swings = swing_detect(highs, lows, k=1)
    kinds = {(s.index, s.kind) for s in swings}
    assert (2, "low") in kinds
    assert (5, "high") in kinds


def test_swing_detect_k_bar_neighborhood_excludes_edges() -> None:
    # peak at index 1 with k=2 cannot be a pivot: only 1 bar precedes it
    highs = [10, 12, 10, 10, 10, 10, 10]
    lows = [9, 11, 9, 9, 9, 9, 9]
    assert swing_detect(highs, lows, k=2) == []


def test_swing_detect_equal_bars_not_pivot() -> None:
    # strict inequality: an equal-high plateau is not a pivot
    highs = [10, 12, 12, 12, 10, 10, 10]
    lows = [9, 11, 11, 11, 9, 9, 9]
    assert swing_detect(highs, lows, k=1) == []


# ---------------------------------------------------------------- ATR


def test_true_range_basic() -> None:
    highs = [12, 12, 10]
    lows = [10, 11, 8]
    closes = [11, 11.5, 9]
    trs = true_range(highs, lows, closes)
    assert trs[0] == pytest.approx(2.0)
    # bar1: max(1, |12-11|=1, |11-11|=0) = 1
    assert trs[1] == pytest.approx(1.0)
    # bar2: max(2, |10-11.5|=1.5, |8-11.5|=3.5) = 3.5
    assert trs[2] == pytest.approx(3.5)


def test_atr_series_window() -> None:
    highs = [2, 2, 2, 2]
    lows = [1, 1, 1, 1]
    closes = [1.5, 1.5, 1.5, 1.5]
    atrs = atr_series(highs, lows, closes, period=2)
    assert atrs[0] == pytest.approx(1.0)
    assert atrs[3] == pytest.approx(1.0)


# ---------------------------------------------------------------- breakout


def _const_atr(n: int, value: float = 1.0) -> list[float]:
    return [value] * n


def test_breakout_events_up_and_once() -> None:
    # swing high at index 2 (price 12); closes stay below then cross
    highs = [10, 10, 12, 10, 10, 10]
    lows = [9, 9, 11, 9, 9, 9]
    closes = [10, 10, 10, 10, 12.2, 12.3]
    atrs = _const_atr(len(closes), 1.0)
    swings = [SwingPoint(2, 12.0, "high")]
    events = breakout_events(highs, lows, closes, atrs, swings, atr_mult=0.15)
    # threshold = 12 + 0.15; close[4]=12.2 > 12.15 → trigger at 4
    assert len(events) == 1
    assert events[0].trigger_index == 4
    assert events[0].direction == "up"
    assert events[0].threshold == pytest.approx(12.15)


def test_breakout_threshold_not_met() -> None:
    highs = [10, 10, 12, 10]
    lows = [9, 9, 11, 9]
    closes = [10, 10, 10, 12.1]
    atrs = _const_atr(4, 1.0)
    swings = [SwingPoint(2, 12.0, "high")]
    events = breakout_events(highs, lows, closes, atrs, swings, atr_mult=0.15)
    # 12.1 <= 12.15 → no event
    assert events == []


def test_breakout_each_swing_fires_once() -> None:
    highs = [10, 10, 12, 10, 10, 10, 10]
    lows = [9, 9, 11, 9, 9, 9, 9]
    closes = [10, 10, 10, 10, 12.2, 12.3, 12.4]
    atrs = _const_atr(len(closes), 1.0)
    swings = [SwingPoint(2, 12.0, "high")]
    events = breakout_events(highs, lows, closes, atrs, swings, atr_mult=0.15)
    assert len(events) == 1  # first qualifying close wins


def test_breakout_down_direction() -> None:
    highs = [10, 10, 8, 10]
    lows = [9, 9, 7, 9]
    closes = [9, 9, 9, 7.8]
    atrs = _const_atr(4, 1.0)
    swings = [SwingPoint(2, 8.0, "low")]
    events = breakout_events(highs, lows, closes, atrs, swings, atr_mult=0.15)
    assert len(events) == 1
    assert events[0].direction == "down"
    assert events[0].threshold == pytest.approx(7.85)


# ---------------------------------------------------------------- survival


def test_survival_count_stays_above() -> None:
    closes = [10, 10, 10, 10, 12.2, 12.3, 12.4, 12.5, 12.6]
    event = BreakoutEvent(3, 12.0, "up", 12.15)
    surv, evald = survival_count(closes, [event], window=3)
    assert evald == 1
    assert surv == 1


def test_survival_count_fails_back_through() -> None:
    closes = [10, 10, 10, 10, 12.2, 12.3, 11.0, 12.5, 12.6]
    event = BreakoutEvent(3, 12.0, "up", 12.15)
    surv, evald = survival_count(closes, [event], window=3)
    # close[6]=11.0 < 12.0 within window → fail
    assert evald == 1
    assert surv == 0


def test_survival_tail_excluded() -> None:
    closes = [10, 10, 10, 10, 12.2, 12.3]
    event = BreakoutEvent(3, 12.0, "up", 12.15)
    surv, evald = survival_count(closes, [event], window=3)
    # trigger+window = 6 >= len(6) → excluded
    assert evald == 0
    assert surv == 0


# ---------------------------------------------------------------- MM


def test_mm_hit_up_touch() -> None:
    highs = [10, 10, 10, 12, 12.5, 13.5, 10]
    lows = [9, 9, 9, 11, 11.9, 12.6, 9]
    closes = [10, 10, 10, 10, 12.2, 13.2, 10]
    atrs = _const_atr(len(closes), 1.0)
    event = BreakoutEvent(4, 12.0, "up", 12.15)
    # leg = |12.2 - 12.0| = 0.2 → target 12.4; high[5]=13.5 >= 12.4-0.5
    res = mm_hit(highs, lows, closes, [event], window=2, atrs=atrs)
    assert res.evaluated == 1
    assert res.hits == 1


def test_mm_miss_beyond_window() -> None:
    highs = [10, 10, 10, 12, 12.5, 12.21, 10]
    lows = [9, 9, 9, 11, 11.9, 11.9, 9]
    closes = [10, 10, 10, 10, 12.2, 12.0, 10]
    atrs = _const_atr(len(closes), 0.1)
    event = BreakoutEvent(4, 12.0, "up", 12.15)
    # leg=0.2, target 12.4, tol=0.05; high[5]=12.21 < 12.35; window=1 only bar5
    res = mm_hit(highs, lows, closes, [event], window=1, atrs=atrs)
    assert res.hits == 0


# ---------------------------------------------------------------- spikes


def test_spike_counter_basic() -> None:
    # 3 consecutive up bars with big combined range
    opens = [10, 10.5, 11, 11.5, 11.5]
    highs = [10.5, 11, 11.5, 12, 12]
    lows = [10, 10.5, 11, 11.5, 11.5]
    closes = [10.5, 11, 11.5, 12, 11.5]
    atrs = [1.0] * 5
    spikes = spike_counter(opens, highs, lows, closes, atrs, min_bars=3, range_mult=1.5)
    assert len(spikes) == 1
    assert spikes[0].direction == "up"
    assert spikes[0].start_index == 0
    assert spikes[0].end_index == 3


def test_spike_counter_needs_min_bars() -> None:
    opens = [10, 10.5, 10.5]
    highs = [10.5, 11, 11]
    lows = [10, 10.5, 10.5]
    closes = [10.5, 11, 11]
    atrs = [1.0] * 3
    spikes = spike_counter(opens, highs, lows, closes, atrs, min_bars=3, range_mult=1.5)
    assert spikes == []


def test_spike_reversal_rate() -> None:
    closes = [10, 10.5, 11, 11.5, 12, 10.8, 10.5, 10.2, 10.0]
    atrs = [1.0] * len(closes)

    spike = SpikeEvent(0, 3, "up")
    rev, evald = spike_reversal_rate(closes, atrs, [spike], window=4, atr_move=1.0)
    # ref = close[3] = 11.5; closes 4..7: 12, 10.8, 10.5, 10.2 → 10.5 <= 10.5 → yes
    assert evald == 1
    assert rev == 1


# ---------------------------------------------------------------- edge


def test_direction_edge_all_hits() -> None:
    closes = [10, 11, 12, 13, 14]
    dirs = ["up", "up", "up", "up", ""]
    r = direction_edge(closes, dirs)
    assert r["n"] == 4
    assert r["hit_rate"] == pytest.approx(1.0)
    assert r["edge_vs_coin"] == pytest.approx(0.5)
    assert r["edge_after_cost"] == pytest.approx(0.5 - 0.003)


def test_direction_edge_coin_flip() -> None:
    closes = [10, 11, 10, 11, 10]
    dirs = ["up", "up", "up", "up", ""]  # constant guess vs alternating actual
    r = direction_edge(closes, dirs)
    assert r["n"] == 4
    assert r["hit_rate"] == pytest.approx(0.5)
    assert r["edge_after_cost"] == pytest.approx(-0.003)


def test_direction_edge_no_calls() -> None:
    closes = [10, 11, 12]
    dirs = ["", "", ""]
    r = direction_edge(closes, dirs)
    assert r["n"] == 0
    assert r["hit_rate"] == 0.0


# ---------------------------------------------------------------- CI


def test_wilson_ci_bounds() -> None:
    lo, hi = wilson_ci(62, 100)
    assert 0.5 < lo < 0.63
    assert 0.62 < hi < 0.72
    lo0, hi0 = wilson_ci(0, 0)
    assert lo0 == 0.0 and hi0 == 0.0


def test_wilson_ci_degenerate() -> None:
    lo, hi = wilson_ci(5, 5)
    assert lo > 0.5
    assert hi == pytest.approx(1.0)

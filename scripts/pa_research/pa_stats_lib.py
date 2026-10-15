"""PA statistics library — pure functions over OHLCV arrays, no IO.

Method provenance: the statistical questions (swing-point structure,
breakout survival, measured-move projection, spike behavior) come from
Al Brooks' published methodology — "Reading Price Charts Bar by Bar"
(2009) and the Brooks Trading Course companion volumes (2011). Parameter
grids are project-chosen constants; every number this library produces is
computed from selene's own data. No text or code from any external
implementation is reproduced here (see PROVENANCE.md in this directory).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


def true_range(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> list[float]:
    """TR[i] for i>=1 using the previous close; TR[0] = high-low."""
    out: list[float] = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        pc = closes[i - 1]
        out.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - pc),
                abs(lows[i] - pc),
            )
        )
    return out


def atr_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> list[float]:
    """Simple rolling mean of true_range; first valid value at index 0
    uses whatever bars exist (min(period, available))."""
    trs = true_range(highs, lows, closes)
    out: list[float] = []
    for i in range(len(trs)):
        window = trs[max(0, i - period + 1) : i + 1]
        out.append(sum(window) / len(window))
    return out


@dataclass(frozen=True)
class SwingPoint:
    index: int
    price: float
    kind: str  # "high" | "low"


def swing_detect(
    highs: Sequence[float],
    lows: Sequence[float],
    k: int = 3,
) -> list[SwingPoint]:
    """Fractal pivots: a swing high at i requires the k bars on each side
    to have strictly lower highs; swing low mirrors it. The first/last k
    bars cannot be pivots (incomplete neighborhood)."""
    n = len(highs)
    out: list[SwingPoint] = []
    for i in range(k, n - k):
        is_high = all(highs[i] > highs[i - j] for j in range(1, k + 1)) and all(
            highs[i] > highs[i + j] for j in range(1, k + 1)
        )
        is_low = all(lows[i] < lows[i - j] for j in range(1, k + 1)) and all(
            lows[i] < lows[i + j] for j in range(1, k + 1)
        )
        if is_high:
            out.append(SwingPoint(i, highs[i], "high"))
        elif is_low:
            out.append(SwingPoint(i, lows[i], "low"))
    return out


@dataclass(frozen=True)
class BreakoutEvent:
    trigger_index: int
    level: float
    direction: str  # "up" | "down"
    threshold: float  # level + atr_mult * atr at trigger (up) or minus (down)


def breakout_events(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    atrs: Sequence[float],
    swings: Sequence[SwingPoint],
    atr_mult: float = 0.15,
) -> list[BreakoutEvent]:
    """A breakout closes beyond a prior swing level by atr_mult * ATR.

    Each swing can break at most once (first qualifying close wins);
    events are returned in trigger order.
    """
    out: list[BreakoutEvent] = []
    used: set[int] = set()
    for i in range(1, len(closes)):
        atr = atrs[i] if atrs[i] > 0 else 0.0
        for s in swings:
            if s.index >= i or s.index in used:
                continue
            if s.kind == "high":
                level = s.price + atr_mult * atr
                if closes[i] > level:
                    out.append(BreakoutEvent(i, s.price, "up", level))
                    used.add(s.index)
            else:
                level = s.price - atr_mult * atr
                if closes[i] < level:
                    out.append(BreakoutEvent(i, s.price, "down", level))
                    used.add(s.index)
    out.sort(key=lambda e: e.trigger_index)
    return out


def survival_count(
    closes: Sequence[float],
    events: Sequence[BreakoutEvent],
    window: int,
) -> tuple[int, int]:
    """A breakout survives if within `window` bars after the trigger no
    close crosses back through the broken swing level. Returns
    (survivors, evaluated); events near the data tail (fewer than
    `window` bars left) are excluded from evaluation."""
    survivors = 0
    evaluated = 0
    n = len(closes)
    for e in events:
        end = e.trigger_index + window
        if end >= n:
            continue
        evaluated += 1
        survived = True
        for j in range(e.trigger_index + 1, end + 1):
            if e.direction == "up" and closes[j] < e.level:
                survived = False
                break
            if e.direction == "down" and closes[j] > e.level:
                survived = False
                break
        if survived:
            survivors += 1
    return survivors, evaluated


@dataclass(frozen=True)
class MMResult:
    hits: int
    evaluated: int


def mm_hit(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    events: Sequence[BreakoutEvent],
    window: int,
    atrs: Sequence[float],
    atr_tolerance: float = 0.5,
) -> MMResult:
    """Measured-move projection: leg length = |close at trigger - swing
    level|; projected target extends the same distance beyond the trigger
    close. A hit occurs when price touches the target within `window`
    bars (tolerance atr_tolerance * ATR at trigger)."""
    hits = 0
    evaluated = 0
    n = len(closes)
    for e in events:
        end = e.trigger_index + window
        if end >= n:
            continue
        evaluated += 1
        leg = abs(closes[e.trigger_index] - e.level)
        if leg <= 0:
            continue
        tol = atr_tolerance * atrs[e.trigger_index]
        if e.direction == "up":
            target = closes[e.trigger_index] + leg
            touched = any(
                highs[j] >= target - tol for j in range(e.trigger_index + 1, end + 1)
            )
        else:
            target = closes[e.trigger_index] - leg
            touched = any(
                lows[j] <= target + tol for j in range(e.trigger_index + 1, end + 1)
            )
        if touched:
            hits += 1
    return MMResult(hits, evaluated)


@dataclass(frozen=True)
class SpikeEvent:
    start_index: int
    end_index: int
    direction: str  # "up" | "down"


def spike_counter(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    atrs: Sequence[float],
    min_bars: int = 3,
    range_mult: float = 1.5,
) -> list[SpikeEvent]:
    """A spike is `min_bars`+ consecutive bars in one direction whose
    combined range exceeds range_mult * mean ATR. Direction per bar:
    close > open → up, close < open → down; dojis break the run."""
    runs: list[SpikeEvent] = []
    mean_atr = sum(atrs) / len(atrs) if atrs else 0.0
    threshold = range_mult * mean_atr

    def bar_dir(i: int) -> str | None:
        if closes[i] > opens[i]:
            return "up"
        if closes[i] < opens[i]:
            return "down"
        return None

    i = 0
    n = len(closes)
    while i < n:
        d = bar_dir(i)
        if d is None:
            i += 1
            continue
        j = i
        while j + 1 < n and bar_dir(j + 1) == d:
            j += 1
        length = j - i + 1
        combined = max(highs[i : j + 1]) - min(lows[i : j + 1])
        if length >= min_bars and combined > threshold:
            runs.append(SpikeEvent(i, j, d))
        i = j + 1
    return runs


def spike_reversal_rate(
    closes: Sequence[float],
    atrs: Sequence[float],
    spikes: Sequence[SpikeEvent],
    window: int = 8,
    atr_move: float = 1.0,
) -> tuple[int, int]:
    """After a spike ends, how often does price move >= atr_move * ATR
    against the spike direction within `window` bars? Returns
    (reversals, evaluated); spikes near the tail are excluded."""
    reversals = 0
    evaluated = 0
    n = len(closes)
    for s in spikes:
        end = s.end_index + window
        if end >= n:
            continue
        evaluated += 1
        atr = atrs[s.end_index]
        ref = closes[s.end_index]
        if s.direction == "up":
            reversed_ = any(
                closes[j] <= ref - atr_move * atr
                for j in range(s.end_index + 1, end + 1)
            )
        else:
            reversed_ = any(
                closes[j] >= ref + atr_move * atr
                for j in range(s.end_index + 1, end + 1)
            )
        if reversed_:
            reversals += 1
    return reversals, evaluated


def direction_edge(
    closes: Sequence[float],
    predicted_dirs: Sequence[str],
    fee_rate: float = 0.001,
    slip_rate: float = 0.0005,
) -> dict[str, float]:
    """Directional hit rate over 1-bar-ahead closes minus coin-flip
    baseline minus round-trip costs. predicted_dirs[i] predicts
    closes[i+1] > closes[i] ("up") or below ("down"); "" = no call."""
    assert len(closes) == len(predicted_dirs)
    hits = 0
    calls = 0
    for i in range(len(closes) - 1):
        p = predicted_dirs[i]
        if p not in ("up", "down"):
            continue
        calls += 1
        actual_up = closes[i + 1] > closes[i]
        if (p == "up") == actual_up:
            hits += 1
    if calls == 0:
        return {"n": 0, "hit_rate": 0.0, "edge_vs_coin": 0.0, "edge_after_cost": 0.0}
    hit_rate = hits / calls
    edge = hit_rate - 0.5
    cost = 2 * fee_rate + 2 * slip_rate
    return {
        "n": calls,
        "hit_rate": hit_rate,
        "edge_vs_coin": edge,
        "edge_after_cost": edge - cost,
    }


def wilson_ci(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denom
    return (max(0.0, center - half), min(1.0, center + half))


__all__ = [
    "SwingPoint",
    "BreakoutEvent",
    "SpikeEvent",
    "MMResult",
    "true_range",
    "atr_series",
    "swing_detect",
    "breakout_events",
    "survival_count",
    "mm_hit",
    "spike_counter",
    "spike_reversal_rate",
    "direction_edge",
    "wilson_ci",
]

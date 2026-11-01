"""ICT-style price-structure detectors (cleanroom rewrite per REWRITE_SPEC_ICT.md).

Four registry-backed indicators covering core Inner-Circle-Trader concepts:

- :class:`FVG` — three-candle imbalance zones (fair value gaps);
- :class:`OrderBlockDetector` — last opposing candle before an impulsive move;
- :class:`LiquiditySweepDetector` — stop-hunts: prior-extreme breach on heavy
  volume with a fast reversal, signalled on the confirmation bar;
- :class:`MarketStructure` — swing-sequence trend labeling (HH/HL vs LH/LL).

All ``calculate()`` implementations emit **directional incremental** values —
``+1`` when a bullish structure forms on that bar, ``-1`` when a bearish one
does, ``0`` on post-warm-up bars with no new structure, and NaN while the
lookback is unsatisfied (the :class:`Indicator` warm-up contract). The
runtime's ``exists`` operator treats "value > 0" as true, so bullish legs see
exactly the ``+1`` bars; direction-explicit legs use ``greater_than 0`` /
``less_than 0`` on the same series.

Every detector is causal: bar ``i``'s label depends only on data at or
before ``i`` (MarketStructure additionally requires the full right radius
of a swing to have elapsed before that swing is used).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator


# ---------------------------------------------------------------------------
# Structure records
# ---------------------------------------------------------------------------


@dataclass
class FVGZone:
    """One detected fair-value-gap rectangle."""

    type: Literal["bullish", "bearish"]
    upper: float
    lower: float
    mid: float
    bar_index: int
    filled: bool = False


@dataclass
class OrderBlock:
    """One detected order-block candle range (open-to-wick boundaries)."""

    type: Literal["bullish", "bearish"]
    upper: float
    lower: float
    bar_index: int
    mitigated: bool = False


@dataclass
class LiquiditySweep:
    """One confirmed stop-hunt event."""

    type: Literal["bullish_sweep", "bearish_sweep"]
    level: float
    bar_index: int
    volume_ratio: float
    reversal_confirmed: bool = False


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Per-bar true range across three gap scenarios."""
    previous_close = close.shift(1)
    candidates = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    )
    return candidates.max(axis=1)


def _directional_series(index: pd.Index) -> pd.Series:
    """Float signal series pre-filled with NaN (warm-up contract)."""
    return pd.Series(np.nan, index=index, dtype=float)


# ---------------------------------------------------------------------------
# FVG
# ---------------------------------------------------------------------------


class FVG(Indicator):
    """Fair Value Gap detector.

    A gap exists at bar *i* when candle *i-2* and candle *i* leave empty
    space between them: bullish when ``high[i-2] < low[i]`` (unfinished
    business below), bearish when ``low[i-2] > high[i]``. Gaps smaller than
    ``min_gap_atr`` × ATR are ignored as noise.

    The zone rectangle is ``[high[i-2], low[i]]`` for a bullish gap and
    ``[high[i], low[i-2]]`` for a bearish one; ``mid`` records the midpoint
    (the consequent encroachment observation level).
    """

    def __init__(self, period: int = 3, min_gap_atr: float = 0.1) -> None:
        """``period`` drives the ATR smoothing window; ``min_gap_atr`` is the
        minimum gap width expressed as a fraction of ATR."""
        super().__init__(period)
        self._min_gap_atr = min_gap_atr
        self._zones: list[FVGZone] = []

    @property
    def name(self) -> str:
        return "fvg"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """+1/-1 on the bar opening a new bullish/bearish gap, else 0/NaN."""
        highs, lows, closes = data["high"], data["low"], data["close"]

        tr = _true_range(highs, lows, closes)
        # First bar has no previous close: its ATR is NaN by contract.
        atr = tr.rolling(window=self._period, min_periods=self._period).mean()

        signal = _directional_series(data.index)
        signal.iloc[2:] = 0
        self._zones = []

        for i in range(2, len(data)):
            noise_floor = float(atr.iloc[i]) * self._min_gap_atr
            if np.isnan(noise_floor):
                signal.iloc[i] = np.nan
                continue

            older_high = float(highs.iloc[i - 2])
            older_low = float(lows.iloc[i - 2])
            newer_high = float(highs.iloc[i])
            newer_low = float(lows.iloc[i])

            if older_high < newer_low and (newer_low - older_high) >= noise_floor:
                self._zones.append(
                    FVGZone(
                        type="bullish",
                        upper=newer_low,
                        lower=older_high,
                        mid=(older_high + newer_low) / 2,
                        bar_index=i,
                    )
                )
                signal.iloc[i] = 1
            elif older_low > newer_high and (older_low - newer_high) >= noise_floor:
                self._zones.append(
                    FVGZone(
                        type="bearish",
                        upper=older_low,
                        lower=newer_high,
                        mid=(older_low + newer_high) / 2,
                        bar_index=i,
                    )
                )
                signal.iloc[i] = -1

        return signal

    def get_active_zones(
        self, current_price: float, max_age: int = 50
    ) -> list[FVGZone]:
        """Zones not yet traded through and young enough to matter.

        A zone closed beyond its far edge is marked filled as a side effect
        and excluded thereafter; stale zones (by detection-order distance)
        are skipped without being mutated.
        """
        live: list[FVGZone] = []
        newest = len(self._zones) - 1
        for pos, zone in enumerate(self._zones):
            if zone.filled:
                continue
            if zone.type == "bullish" and current_price < zone.lower:
                zone.filled = True
                continue
            if zone.type == "bearish" and current_price > zone.upper:
                zone.filled = True
                continue
            if newest - pos > max_age:
                continue
            live.append(zone)
        return live


# ---------------------------------------------------------------------------
# Order blocks
# ---------------------------------------------------------------------------


class OrderBlockDetector(Indicator):
    """Last opposing candle before an outsized directional move.

    When bar *i* travels at least ``move_threshold`` × average range versus
    the prior close, the most recent opposite-color candle within the
    lookback is recorded as the institutional footprint. The impulse bar *i*
    itself carries the signal. Zone boundaries are the textbook open-to-wick
    pair: a bullish block spans ``[low[j], open[j]]``, a bearish one
    ``[open[j], high[j]]``.
    """

    _SEARCH_DEPTH = 10

    def __init__(self, period: int = 20, move_threshold: float = 2.0) -> None:
        """``period`` sets the average-range window; ``move_threshold`` scales it
        into the minimum impulse size."""
        super().__init__(period)
        self._move_threshold = move_threshold
        self._blocks: list[OrderBlock] = []

    @property
    def name(self) -> str:
        return "order_block"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """+1/-1 on bullish/bearish displacement bars, else 0/NaN."""
        opens, highs, lows, closes = (
            data["open"],
            data["high"],
            data["low"],
            data["close"],
        )

        typical_range = (
            (highs - lows).rolling(window=self._period, min_periods=self._period).mean()
        )

        signal = _directional_series(data.index)
        signal.iloc[self._period :] = 0
        self._blocks = []

        for i in range(self._period, len(data)):
            avg_range = float(typical_range.iloc[i])
            if np.isnan(avg_range):
                continue

            displacement = abs(float(closes.iloc[i]) - float(closes.iloc[i - 1]))
            if displacement < avg_range * self._move_threshold:
                continue

            upward_leg = closes.iloc[i] > closes.iloc[i - 1]

            for j in range(i - 1, max(0, i - self._SEARCH_DEPTH) - 1, -1):
                body_down = closes.iloc[j] < opens.iloc[j]
                body_up = closes.iloc[j] > opens.iloc[j]

                if (upward_leg and body_down) or (not upward_leg and body_up):
                    if upward_leg:
                        upper, lower = float(opens.iloc[j]), float(lows.iloc[j])
                    else:
                        upper, lower = float(highs.iloc[j]), float(opens.iloc[j])
                    self._blocks.append(
                        OrderBlock(
                            type="bullish" if upward_leg else "bearish",
                            upper=upper,
                            lower=lower,
                            bar_index=j,
                        )
                    )
                    signal.iloc[i] = 1 if upward_leg else -1
                    break

        return signal

    def get_active_blocks(
        self, current_price: float, max_age: int = 30
    ) -> list[OrderBlock]:
        """Blocks price has not fully consumed yet, within the age budget."""
        live: list[OrderBlock] = []
        newest = len(self._blocks) - 1
        for pos, block in enumerate(self._blocks):
            if block.mitigated:
                continue
            if block.type == "bullish" and current_price < block.lower:
                block.mitigated = True
                continue
            if block.type == "bearish" and current_price > block.upper:
                block.mitigated = True
                continue
            if newest - pos > max_age:
                continue
            live.append(block)
        return live


# ---------------------------------------------------------------------------
# Liquidity sweeps
# ---------------------------------------------------------------------------


class LiquiditySweepDetector(Indicator):
    """Stop-hunt detector: prior-extreme breach + volume spike + fast reversal.

    The prior high/low come from a window that ends at bar ``i-1`` (strictly
    before the raiding bar — a centred window would contain bar *i* itself
    and make the breach test unsatisfiable). A sweep at bar *i* pierces that
    prior extreme with volume above ``volume_threshold`` × its rolling mean;
    the signal lands on bar ``i + reversal_bars`` once either reversal
    evidence is in: the close at the confirmation bar is back on the
    pre-raid side of the swept level, or price has broken the opposite
    extreme inside the window.

    The trailing ``reversal_bars`` rows carry ``0``, not NaN: their
    confirmation window has not elapsed, so they are "evaluated, no signal"
    rather than "insufficient data". NaN there would be fatal downstream —
    :meth:`StrategyRuntime.on_kline` reads only the newest row and bails out
    whenever any indicator column is NaN, so a NaN tail would make this
    detector permanently invisible to every strategy that uses it.
    """

    def __init__(
        self, period: int = 20, volume_threshold: float = 1.5, reversal_bars: int = 3
    ) -> None:
        """``period`` sizes the rolling extremes and volume baseline;
        ``reversal_bars`` bounds how quickly the snap-back must occur."""
        super().__init__(period)
        self._volume_threshold = volume_threshold
        self._reversal_bars = reversal_bars
        self._sweeps: list[LiquiditySweep] = []

    @property
    def name(self) -> str:
        return "liquidity_sweep"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """+1/-1 on bullish/bearish sweep confirmation bars, else 0/NaN."""
        highs, lows = data["high"], data["low"]
        volumes = data.get("volume", pd.Series(1.0, index=data.index))

        mean_volume = volumes.rolling(
            window=self._period, min_periods=self._period
        ).mean()
        prior_high = (
            highs.shift(1).rolling(window=self._period, min_periods=self._period).max()
        )
        prior_low = (
            lows.shift(1).rolling(window=self._period, min_periods=self._period).min()
        )

        signal = _directional_series(data.index)
        signal.iloc[self._period :] = 0
        self._sweeps = []

        last_evaluable = len(data) - self._reversal_bars
        for i in range(self._period, last_evaluable):
            mean_v = float(mean_volume.iloc[i])
            if np.isnan(mean_v) or mean_v <= 0:
                continue
            volume_multiple = float(volumes.iloc[i]) / mean_v
            if volume_multiple < self._volume_threshold:
                continue

            confirm_bar = i + self._reversal_bars
            swept_high = float(prior_high.iloc[i])
            swept_low = float(prior_low.iloc[i])

            if highs.iloc[i] > swept_high:
                # Raided the highs: bearish when the rejection is confirmed —
                # either price snaps back under the swept level by the close
                # of the confirmation bar, or the collapse breaks the prior
                # low inside the window.
                confirm_close = float(data["close"].iloc[confirm_bar])
                collapsed = lows.iloc[i : i + self._reversal_bars + 1].min() < swept_low
                rejected = confirm_close < swept_high
                if collapsed or rejected:
                    self._sweeps.append(
                        LiquiditySweep(
                            type="bearish_sweep",
                            level=swept_high,
                            bar_index=i,
                            volume_ratio=volume_multiple,
                            reversal_confirmed=True,
                        )
                    )
                    signal.iloc[confirm_bar] = -1
            elif lows.iloc[i] < swept_low:
                confirm_close = float(data["close"].iloc[confirm_bar])
                reclaimed = (
                    highs.iloc[i : i + self._reversal_bars + 1].max() > swept_high
                )
                rejected = confirm_close > swept_low
                if reclaimed or rejected:
                    self._sweeps.append(
                        LiquiditySweep(
                            type="bullish_sweep",
                            level=swept_low,
                            bar_index=i,
                            volume_ratio=volume_multiple,
                            reversal_confirmed=True,
                        )
                    )
                    signal.iloc[confirm_bar] = 1

        return signal

    def get_recent_sweeps(self, max_age: int = 10) -> list[LiquiditySweep]:
        """Confirmed sweeps within ``max_age`` detections of the newest one.

        ``max_age`` counts detections, not bars: ``bar_index`` is an absolute
        row position, so measuring against it would make the budget depend on
        how many bars happened to pass rather than how many sweeps did.
        """
        newest = len(self._sweeps) - 1
        return [
            sweep
            for pos, sweep in enumerate(self._sweeps)
            if sweep.reversal_confirmed and newest - pos <= max_age
        ]


# ---------------------------------------------------------------------------
# Market structure
# ---------------------------------------------------------------------------


class MarketStructure(Indicator):
    """Trend state from the sequence of confirmed fractal swing points.

    A swing high at bar *k* is a bar whose high is the maximum of the window
    ``[k-period, k+period]`` and whose full right radius has elapsed
    (``k + period < len``). Labels are +1 while the last two confirmed swing
    highs and lows both rise (HH+HL), -1 while both fall (LH+LL), 0
    otherwise. Classification at bar *i* only uses swings with
    ``swing_idx + period <= i``, so no label can depend on data past bar *i*;
    recomputing on any prefix therefore reproduces the full-series labels.
    """

    def __init__(self, period: int = 10) -> None:
        """Bars on each side required to confirm a swing point."""
        super().__init__(period)
        self._swing_highs: list[tuple[int, float]] = []
        self._swing_lows: list[tuple[int, float]] = []

    @property
    def name(self) -> str:
        return "market_structure"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Series of +1 (uptrend), -1 (downtrend), 0/NaN as described."""
        highs, lows = data["high"], data["low"]
        n = len(data)

        trend = _directional_series(data.index)
        trend.iloc[2 * self._period :] = 0
        self._swing_highs = []
        self._swing_lows = []

        for k in range(self._period, n - self._period):
            window_high = highs.iloc[k - self._period : k + self._period + 1].max()
            window_low = lows.iloc[k - self._period : k + self._period + 1].min()
            if highs.iloc[k] == window_high:
                self._swing_highs.append((k, float(highs.iloc[k])))
            if lows.iloc[k] == window_low:
                self._swing_lows.append((k, float(lows.iloc[k])))

        for i in range(2 * self._period, n):
            confirmed_highs = [
                (idx, h) for idx, h in self._swing_highs if idx + self._period <= i
            ][-2:]
            confirmed_lows = [
                (idx, lo) for idx, lo in self._swing_lows if idx + self._period <= i
            ][-2:]
            if len(confirmed_highs) < 2 or len(confirmed_lows) < 2:
                continue

            higher_highs = confirmed_highs[1][1] > confirmed_highs[0][1]
            higher_lows = confirmed_lows[1][1] > confirmed_lows[0][1]
            lower_highs = confirmed_highs[1][1] < confirmed_highs[0][1]
            lower_lows = confirmed_lows[1][1] < confirmed_lows[0][1]

            if higher_highs and higher_lows:
                trend.iloc[i] = 1
            elif lower_highs and lower_lows:
                trend.iloc[i] = -1
            else:
                trend.iloc[i] = 0

        return trend

    def get_latest_swing_high(self) -> tuple[int, float] | None:
        """Most recent confirmed swing high, or None."""
        return self._swing_highs[-1] if self._swing_highs else None

    def get_latest_swing_low(self) -> tuple[int, float] | None:
        """Most recent confirmed swing low, or None."""
        return self._swing_lows[-1] if self._swing_lows else None

    def get_swings(self) -> list[dict]:
        """Full confirmed swing sequence as ``{index, price, kind}`` rows.

        ``kind`` is ``"high"`` or ``"low"``; rows ascend by index. Pure
        exposure of the internal swing tables — no algorithmic change.
        """
        rows = [
            {"index": idx, "price": price, "kind": "high"}
            for idx, price in self._swing_highs
        ]
        rows += [
            {"index": idx, "price": price, "kind": "low"}
            for idx, price in self._swing_lows
        ]
        rows.sort(key=lambda row: (row["index"], row["kind"]))
        return rows


# ---------------------------------------------------------------------------
# Structure layers: recursive tiers + BOS/MSS events (REWRITE_SPEC_ICT_STRUCTURE.md)
# ---------------------------------------------------------------------------


def mark_structure_tiers(swings: list[dict]) -> list[dict]:
    """Annotate a swing sequence with recursive tiers ``st`` / ``it`` / ``lt``.

    On the sub-sequence of swing highs: the base fractals are STH; a swing
    high with a strictly lower STH on **each side** (between its neighboring
    STHs) is an ITH; an ITH with a strictly lower ITH on each side is an LTH.
    Lows are tiered symmetrically and independently. Invariants: ITH ⊂ STH,
    LTH ⊂ ITH.

    **These labels are not final at the swing's confirmation bar.** A tier
    needs a lower same-kind swing on both flanks, so the right flank is only
    known when the *next* swing confirms — later bars can therefore promote
    ``st`` to ``it``, or ``it`` to ``lt``. Measured on random walks: 13 of 32
    swings were promoted after their own confirmation. The label never moves
    *down* (checked over 11,496 observation steps: zero downgrades) and a
    swing's final label is stable once a higher same-kind swing appears to
    its right, but until then it can still change.

    Consequences: this is a **descriptive/labelling** function for charting.
    Do not feed its output into a bar-close strategy as if the tier at bar
    *i* were knowable at bar *i* — for a causal signal, read the tiers of
    swings confirmed more than one swing in the past, or treat the tier as
    "as of the newest confirmed swing".
    """
    result = [dict(row) | {"tier": "st"} for row in swings]

    def _tier_kind(kind: str) -> None:
        for tier_label, source_tier in (("it", "st"), ("lt", "it")):
            marked = [
                row
                for row in result
                if row["kind"] == kind and row["tier"] == source_tier
            ]
            for position, row in enumerate(marked):
                left = [m["price"] for m in marked[:position]]
                right = [m["price"] for m in marked[position + 1 :]]
                if not left or not right:
                    continue
                if row["kind"] == "high":
                    has_lower_left = any(price < row["price"] for price in left)
                    has_lower_right = any(price < row["price"] for price in right)
                else:
                    has_lower_left = any(price > row["price"] for price in left)
                    has_lower_right = any(price > row["price"] for price in right)
                if has_lower_left and has_lower_right:
                    row["tier"] = tier_label

    _tier_kind("high")
    _tier_kind("low")
    return result


def detect_structure_events(
    data: pd.DataFrame,
    swings: list[dict],
    break_on: Literal["close", "wick"] = "close",
) -> list[dict]:
    """BOS/MSS event table from a swing sequence.

    BOS = close (or wick, per ``break_on``) beyond a swing in the current
    trend's direction (continuation); MSS = breach against the trend
    (reversal — the event most texts call CHoCH). Trend state starts
    neutral (0); the first breach sets it. Each swing breaks at most once.
    Displacement evidence (break-bar body ratio + FVG within 3 bars) is
    attached as metadata and never filters events.
    """
    opens, highs, lows, closes = (
        data["open"],
        data["high"],
        data["low"],
        data["close"],
    )

    highs_seq = [row for row in swings if row["kind"] == "high"]
    lows_seq = [row for row in swings if row["kind"] == "low"]
    broken: set[int] = set()
    trend = 0
    events: list[dict] = []

    fvg = FVG(period=3, min_gap_atr=0.0)
    fvg.calculate(data)

    def _displacement(bar_index: int) -> dict:
        body = abs(float(closes.iloc[bar_index]) - float(opens.iloc[bar_index]))
        span = float(highs.iloc[bar_index]) - float(lows.iloc[bar_index])
        body_ratio = round(body / span, 4) if span > 0 else 0.0
        subsequent = [
            zone
            for zone in fvg._zones
            if zone.bar_index > bar_index and zone.bar_index <= bar_index + 3
        ]
        if subsequent:
            zone = subsequent[0]
            return {
                "body_ratio": body_ratio,
                "has_fvg": True,
                "fvg_range": round(zone.upper - zone.lower, 8),
            }
        return {"body_ratio": body_ratio, "has_fvg": False, "fvg_range": 0.0}

    for i in range(len(data)):
        breach_up = (
            float(closes.iloc[i]) if break_on == "close" else float(highs.iloc[i])
        )
        breach_down = (
            float(closes.iloc[i]) if break_on == "close" else float(lows.iloc[i])
        )

        for row in highs_seq:
            if row["index"] in broken or row["index"] > i:
                continue
            if breach_up > row["price"]:
                broken.add(row["index"])
                direction = 1
                kind = "BOS" if trend >= 0 else "MSS"
                if trend == 0:
                    trend = 1
                elif kind == "MSS":
                    trend = 1
                events.append(
                    {
                        "kind": kind,
                        "direction": direction,
                        "level": float(row["price"]),
                        "broken_bar_index": i,
                        "swing_bar_index": row["index"],
                        "displacement": _displacement(i),
                    }
                )
                break

        for row in lows_seq:
            if row["index"] in broken or row["index"] > i:
                continue
            if breach_down < row["price"]:
                broken.add(row["index"])
                direction = -1
                kind = "BOS" if trend <= 0 else "MSS"
                if trend == 0:
                    trend = -1
                elif kind == "MSS":
                    trend = -1
                events.append(
                    {
                        "kind": kind,
                        "direction": direction,
                        "level": float(row["price"]),
                        "broken_bar_index": i,
                        "swing_bar_index": row["index"],
                        "displacement": _displacement(i),
                    }
                )
                break

    return events

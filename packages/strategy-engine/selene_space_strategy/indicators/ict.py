"""ICT-style price-structure detectors.

Four registry-backed indicators covering core Inner-Circle-Trader concepts:

- :class:`FVG` — three-candle imbalance zones (fair value gaps);
- :class:`OrderBlockDetector` — last opposing candle before an impulsive move;
- :class:`LiquiditySweepDetector` — stop-hunts: swing breach on heavy volume
  followed by a quick reversal;
- :class:`MarketStructure` — swing-sequence trend labeling (HH/HL vs LH/LL).

All ``calculate()`` implementations emit **incremental** values (a 1 only on
the bar where a fresh structure appears, 0 elsewhere) rather than running
counts — the runtime's ``exists`` operator treats "value > 0" as true, so any
cumulative encoding would latch entries permanently after the first trigger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
    """One detected order-block candle range."""

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


# ---------------------------------------------------------------------------
# FVG
# ---------------------------------------------------------------------------


class FVG(Indicator):
    """Fair Value Gap detector.

    A gap exists at bar *i* when candle *i-2* and candle *i* leave empty
    space between them: bullish when ``high[i-2] < low[i]`` (unfinished
    business below), bearish when ``low[i-2] > high[i]``. Gaps smaller than
    ``min_gap_atr`` × ATR are ignored as noise.
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
        """1 on bars opening a new qualifying gap, else 0."""
        highs, lows, closes = data["high"], data["low"], data["close"]

        atr = _true_range(highs, lows, closes).rolling(window=self._period, min_periods=1).mean()

        signal = pd.Series(0, index=data.index, dtype=int)
        self._zones = []

        for i in range(2, len(data)):
            older_high = highs.iloc[i - 2]
            older_low = lows.iloc[i - 2]
            newer_high = highs.iloc[i]
            newer_low = lows.iloc[i]

            noise_floor = float(atr.iloc[i]) * self._min_gap_atr

            if older_high < newer_low:
                # Bullish imbalance: room between old high and new low.
                if (newer_low - older_high) >= noise_floor:
                    midpoint = (older_high + newer_low) / 2
                    self._zones.append(
                        FVGZone(type="bullish", upper=newer_low, lower=older_high, mid=midpoint, bar_index=i)
                    )
                    signal.iloc[i] = 1

            elif older_low > newer_high:
                # Bearish imbalance: room between old low and new high.
                if (older_low - newer_high) >= noise_floor:
                    midpoint = (older_low + newer_high) / 2
                    self._zones.append(
                        FVGZone(type="bearish", upper=older_low, lower=newer_high, mid=midpoint, bar_index=i)
                    )
                    signal.iloc[i] = 1

        return signal

    def get_active_zones(self, current_price: float, max_age: int = 50) -> list[FVGZone]:
        """Zones not yet traded through and young enough to matter.

        A zone touched beyond its far edge is marked filled as a side effect
        and excluded thereafter; stale zones (by detection order distance)
        are skipped without being mutated.
        """
        live: list[FVGZone] = []
        for zone in self._zones:
            if zone.filled:
                continue
            if zone.type == "bullish" and current_price <= zone.lower:
                zone.filled = True
                continue
            if zone.type == "bearish" and current_price >= zone.upper:
                zone.filled = True
                continue
            if len(self._zones) - zone.bar_index > max_age:
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
    itself carries the incremental signal.
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
        """1 on impulse bars that found an opposing candle behind them, else 0."""
        opens, highs, lows, closes = data["open"], data["high"], data["low"], data["close"]

        typical_range = (highs - lows).rolling(window=self._period, min_periods=1).mean()

        signal = pd.Series(0, index=data.index, dtype=int)
        self._blocks = []

        for i in range(self._period, len(data)):
            displacement = abs(closes.iloc[i] - closes.iloc[i - 1])
            if displacement < float(typical_range.iloc[i]) * self._move_threshold:
                continue

            upward_leg = closes.iloc[i] > closes.iloc[i - 1]

            for j in range(i - 1, max(0, i - self._SEARCH_DEPTH), -1):
                body_up = closes.iloc[j] > opens.iloc[j]
                body_down = closes.iloc[j] < opens.iloc[j]

                if (upward_leg and body_down) or (not upward_leg and body_up):
                    self._blocks.append(
                        OrderBlock(
                            type="bullish" if upward_leg else "bearish",
                            upper=highs.iloc[j],
                            lower=lows.iloc[j],
                            bar_index=j,
                        )
                    )
                    signal.iloc[i] = 1
                    break

        return signal

    def get_active_blocks(self, current_price: float, max_age: int = 30) -> list[OrderBlock]:
        """Blocks price has not fully consumed yet, within the age budget."""
        live: list[OrderBlock] = []
        for block in self._blocks:
            if block.mitigated:
                continue
            if block.type == "bullish" and current_price < block.lower:
                block.mitigated = True
                continue
            if block.type == "bearish" and current_price > block.upper:
                block.mitigated = True
                continue
            if len(self._blocks) - block.bar_index > max_age:
                continue
            live.append(block)
        return live


# ---------------------------------------------------------------------------
# Liquidity sweeps
# ---------------------------------------------------------------------------


class LiquiditySweepDetector(Indicator):
    """Stop-hunt detector: swing breach + volume spike + fast reversal.

    A sweep at bar *i* pierces the prior rolling extreme with volume above
    ``volume_threshold`` × its rolling mean; the signal lands on bar
    ``i + reversal_bars`` once price has snapped back through the opposite
    swing within that many bars.
    """

    def __init__(self, period: int = 20, volume_threshold: float = 1.5, reversal_bars: int = 3) -> None:
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
        """1 on confirmation bars of confirmed sweeps, else 0."""
        highs, lows = data["high"], data["low"]
        volumes = data.get("volume", pd.Series(1, index=data.index))

        mean_volume = volumes.rolling(window=self._period, min_periods=1).mean()
        rolling_high = highs.rolling(window=self._period, center=True).max()
        rolling_low = lows.rolling(window=self._period, center=True).min()

        signal = pd.Series(0, index=data.index, dtype=int)
        self._sweeps = []

        last_evaluable = len(data) - self._reversal_bars
        for i in range(self._period, last_evaluable):
            volume_multiple = (
                volumes.iloc[i] / mean_volume.iloc[i] if float(mean_volume.iloc[i]) > 0 else 1.0
            )
            if volume_multiple < self._volume_threshold:
                continue

            confirm_bar = i + self._reversal_bars

            pierced_high = highs.iloc[i] > rolling_high.iloc[i - 1]
            pierced_low = lows.iloc[i] < rolling_low.iloc[i - 1]

            if pierced_high:
                post_window_low = lows.iloc[i : i + self._reversal_bars + 1].min()
                if post_window_low < rolling_low.iloc[i - 1]:
                    # Pushed above the old swing high, then collapsed below
                    # the old swing low: sell-side liquidity taken.
                    self._sweeps.append(
                        LiquiditySweep(
                            type="bearish_sweep",
                            level=rolling_high.iloc[i - 1],
                            bar_index=i,
                            volume_ratio=volume_multiple,
                            reversal_confirmed=True,
                        )
                    )
                    signal.iloc[confirm_bar] = 1

            elif pierced_low:
                post_window_high = highs.iloc[i : i + self._reversal_bars + 1].max()
                if post_window_high > rolling_high.iloc[i - 1]:
                    self._sweeps.append(
                        LiquiditySweep(
                            type="bullish_sweep",
                            level=rolling_low.iloc[i - 1],
                            bar_index=i,
                            volume_ratio=volume_multiple,
                            reversal_confirmed=True,
                        )
                    )
                    signal.iloc[confirm_bar] = 1

        return signal

    def get_recent_sweeps(self, max_age: int = 10) -> list[LiquiditySweep]:
        """Confirmed sweeps within ``max_age`` of the newest record."""
        newest = len(self._sweeps)
        return [
            sweep
            for sweep in self._sweeps
            if sweep.reversal_confirmed and newest - sweep.bar_index <= max_age
        ]


# ---------------------------------------------------------------------------
# Market structure
# ---------------------------------------------------------------------------


class MarketStructure(Indicator):
    """Trend state from the sequence of fractal swing points.

    Labels each bar +1 while the last two swing highs and lows both rise
    (HH+HL), -1 while both fall (LH+LL), 0 otherwise. Swings are bars whose
    value equals the centered rolling extreme over ``2·period+1`` windows.
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
        """Series of +1 (uptrend), -1 (downtrend), 0 (unclassified)."""
        highs, lows = data["high"], data["low"]

        window_span = self._period * 2 + 1
        centered_high = highs.rolling(window=window_span, center=True).max()
        centered_low = lows.rolling(window=window_span, center=True).min()

        trend = pd.Series(0, index=data.index, dtype=int)
        self._swing_highs = []
        self._swing_lows = []

        interior_start = self._period
        interior_stop = len(data) - self._period
        for i in range(interior_start, max(interior_stop, interior_start)):
            if highs.iloc[i] == centered_high.iloc[i]:
                self._swing_highs.append((i, float(highs.iloc[i])))
            if lows.iloc[i] == centered_low.iloc[i]:
                self._swing_lows.append((i, float(lows.iloc[i])))

        classify_from = max(self._period * 2, 4)
        for i in range(classify_from, len(data)):
            highs_before_i = [(idx, h) for idx, h in self._swing_highs if idx < i][-2:]
            lows_before_i = [(idx, lo) for idx, lo in self._swing_lows if idx < i][-2:]
            if len(highs_before_i) < 2 or len(lows_before_i) < 2:
                continue

            higher_highs = highs_before_i[1][1] > highs_before_i[0][1]
            higher_lows = lows_before_i[1][1] > lows_before_i[0][1]
            lower_highs = highs_before_i[1][1] < highs_before_i[0][1]
            lower_lows = lows_before_i[1][1] < lows_before_i[0][1]

            if higher_highs and higher_lows:
                trend.iloc[i] = 1
            elif lower_highs and lower_lows:
                trend.iloc[i] = -1

        return trend

    def get_latest_swing_high(self) -> tuple[int, float] | None:
        """Most recent confirmed swing high, or None."""
        return self._swing_highs[-1] if self._swing_highs else None

    def get_latest_swing_low(self) -> tuple[int, float] | None:
        """Most recent confirmed swing low, or None."""
        return self._swing_lows[-1] if self._swing_lows else None

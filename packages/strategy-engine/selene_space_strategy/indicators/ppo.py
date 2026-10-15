"""PPO indicator — momentum expressed as a percentage, not price units.

Subtracts nothing raw: the fast/slow exponential-average gap is divided
by the slow average and scaled to percent, so readings from a $50 altcoin
and a $60,000 index sit on one common ruler.
"""

from __future__ import annotations

from typing import NamedTuple

import pandas as pd

from selene_space_strategy.indicators.base import Indicator


class PPOResult(NamedTuple):
    """Triple output of :meth:`PPO.calculate_full`.

    ``ppo_line`` holds the percentage spread, ``signal_line`` its
    exponential smoothing, and ``histogram`` their running difference.
    """

    ppo_line: pd.Series
    signal_line: pd.Series
    histogram: pd.Series


class PPO(Indicator):
    """Percentage Price Oscillator, TradingView ``ta.ppo`` compatible.

    Three lengths shape the indicator: a fast exponential leg, the slow
    leg carried by the base-class ``period``, and a smoothing applied to
    the resulting oscillator. Defaults reuse Appel's 12/26/9 layout. The
    fast leg must be strictly shorter than the slow one, and every length
    must be positive — violations abort construction.

    Rows preceding the first complete slow window read as NaN in all
    outputs, keeping the series honest about its own warm-up.
    """

    def __init__(
        self,
        period: int = 26,
        fast_period: int = 12,
        signal_period: int = 9,
    ) -> None:
        super().__init__(period)
        if fast_period <= 0:
            raise ValueError(f"fast_period requires a length above zero, received {fast_period}")
        if signal_period <= 0:
            raise ValueError(f"signal_period requires a length above zero, received {signal_period}")
        if fast_period >= period:
            raise ValueError(
                f"fast_period ({fast_period}) is required to stay less than slow period ({period})"
            )
        self._len_fast = fast_period
        self._len_signal = signal_period

    @property
    def name(self) -> str:
        return "ppo"

    @property
    def fast_period(self) -> int:
        """Short-leg exponential length."""
        return self._len_fast

    @property
    def signal_period(self) -> int:
        """Smoothing length laid over the oscillator."""
        return self._len_signal

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Oscillator line only, sharing ``data``'s index.

        Args:
            data: frame carrying a ``"close"`` column.

        Returns:
            Percent-scale series whose first ``period - 1`` rows are NaN.
        """
        return self.calculate_full(data).ppo_line

    def calculate_full(self, data: pd.DataFrame) -> PPOResult:
        """Oscillator, signal and histogram from a single traversal.

        Args:
            data: frame carrying a ``"close"`` column.

        Returns:
            PPOResult triple, each member NaN until the slow leg warms up.
        """
        closes = data["close"]

        # adjust=False seeds both averages from the opening bar, matching
        # Pine's behaviour bar-for-bar.
        quick_leg = closes.ewm(span=self._len_fast, adjust=False).mean()
        slow_leg = closes.ewm(span=self._period, adjust=False).mean()

        oscillator = quick_leg.sub(slow_leg).div(slow_leg).mul(100.0)
        trigger = oscillator.ewm(span=self._len_signal, adjust=False).mean()
        spread_gap = oscillator - trigger

        cold_rows = self._period - 1
        if cold_rows > 0:
            for track in (oscillator, trigger, spread_gap):
                track.iloc[:cold_rows] = float("nan")

        return PPOResult(oscillator, trigger, spread_gap)

    def __repr__(self) -> str:
        return (
            f"PPO(fast={self._len_fast}, slow={self._period}, signal={self._len_signal})"
        )

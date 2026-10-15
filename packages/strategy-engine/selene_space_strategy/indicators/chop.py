"""CHOP — Choppiness Index (range/trend classifier)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator


class ChoppinessIndex(Indicator):
    """Choppiness Index.

    Measures how "choppy" (range-bound) the market is:

        CHOP = 100 * log10( sum(TR, period) / (max(high, period) - min(low, period)) )
                     / log10(period)

    Bounded to [0, 100]. Following the A3 research reference:
    - CHOP < 38.2  -> trending market (low choppiness)
    - CHOP > 61.8  -> ranging / choppy market
    - 38.2 <= CHOP <= 61.8 -> transitional zone

    Uses the raw True Range sum (not Wilder-smoothed ATR) so the value is a
    pure ratio of path-length to range, matching TradingView's ``ta.chop``.
    """

    TREND_THRESHOLD = 38.2
    CHOPPY_THRESHOLD = 61.8

    def __init__(self, period: int = 14) -> None:
        super().__init__(period)

    @property
    def name(self) -> str:
        return "chop"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Calculate the Choppiness Index.

        Args:
            data: DataFrame with ``"high"``, ``"low"``, and ``"close"`` columns.

        Returns:
            Series of CHOP values in [0, 100], aligned with the input index.
            First ``period - 1`` values and flat-market bars are NaN.
        """
        high = data["high"]
        low = data["low"]
        close = data["close"]

        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)

        tr_sum = tr.rolling(self._period).sum()
        price_range = high.rolling(self._period).max() - low.rolling(self._period).min()

        ratio = tr_sum / price_range.replace(0, np.nan)
        chop = 100.0 * np.log10(ratio) / math.log10(self._period)
        chop = chop.clip(0.0, 100.0)

        # Mask early values where the window isn't full and flat-range bars
        chop.iloc[: self._period - 1] = float("nan")
        return chop

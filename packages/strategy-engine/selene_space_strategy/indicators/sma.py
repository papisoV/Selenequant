"""Simple moving average."""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.indicators.base import Indicator


class SMA(Indicator):
    """Arithmetic mean of the last ``period`` closes.

    The workhorse trend filter every other overlay gets compared against.
    Reading conventions in circulation:

    * price holding above a rising average = uptrend intact;
    * crosses of a short average over a long one signal momentum shifts;
    * flat averages with price whipsawing through them = no edge, stand
      aside.

    Computation notes that matter for correctness:

    * The first ``period - 1`` rows have no complete window and read NaN.
    * A single missing close spoils exactly the ``period`` windows it
      falls inside; once it scrolls out of the trailing view, values go
      back to normal. (An accumulator running sum would poison every row
      after the hole instead — deliberately avoided.)
    * Window summation follows pandas' own rolling kernel, pairwise
      accumulation included, so outputs match
      ``close.rolling(period).mean()`` to the last bit.

    Identical in meaning to TradingView's ``ta.sma(source, length)``.
    """

    def _window_means(self, closes: pd.Series) -> pd.Series:
        """Delegate to the trailing-window mean over ``closes``.

        ``min_periods`` pins the full window length so partial windows at
        the head stay undefined rather than degrading into means of fewer
        bars.
        """
        return closes.rolling(window=self._period, min_periods=self._period).mean()

    @property
    def name(self) -> str:
        return "sma"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Average the close column inside each trailing window.

        Args:
            data: Frame exposing a ``"close"`` column.

        Returns:
            A float64 series named ``"sma"``, index-aligned with the input,
            whose first ``period - 1`` entries are NaN.
        """
        averaged = self._window_means(data["close"])
        return pd.Series(
            averaged.to_numpy(dtype="float64"),
            index=data.index,
            name="sma",
        )

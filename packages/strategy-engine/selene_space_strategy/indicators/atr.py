"""Average True Range with Wilder's recursive smoothing."""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator


def _wilder_weight(span: int) -> float:
    """Per-bar decay for a Wilder span, matching pandas exactly.

    pandas re-derives any ``alpha`` through its center-of-mass form —
    ``com = (1-alpha)/alpha`` then ``weight = 1/(1+com)``. The detour nudges
    the weight by an ulp for certain spans, so repeating it here keeps the
    hand-written recursion bit-equal to ``ewm(alpha=1/span, adjust=False)``.
    """
    alpha = 1.0 / span
    return 1.0 / (1.0 + (1.0 - alpha) / alpha)


class ATR(Indicator):
    """Average True Range (Welles Wilder, 1978).

    The true range of a bar is the widest of:

    * the bar's own span: ``high - low``
    * its gap from the prior close on the upside: ``|high - close_prev|``
    * its gap from the prior close on the downside: ``|low - close_prev|``

    ATR smooths that range with Wilder's recursion — an exponential average
    whose weight is ``1 / period`` — which is what TradingView's ``ta.atr``
    and ``ta.rma`` compute. Typical lookback is 14 bars.
    """

    def _wilder_smooth(self, values: np.ndarray, span: int) -> np.ndarray:
        """Run Wilder's recursive average along the true-range stream.

        Follows ``ewm(alpha=1/span, adjust=False)`` to the bit, holes and
        all: a row without a reading still ages the carried weight once,
        the next reading resumes through a renormalised blend of the aged
        weight, and a reading equal to the running average short-circuits
        the arithmetic altogether.
        """
        if values.size == 0:
            return values.copy()
        weight = _wilder_weight(span)
        per_bar = 1.0 - weight
        smoothed = np.empty_like(values)
        average = values[0]
        warmed = average == average
        smoothed[0] = average
        slack = 1.0
        for pos in range(1, len(values)):
            sample = values[pos]
            usable = sample == sample
            if warmed:
                slack *= per_bar
                if usable:
                    if average != sample:
                        average = slack * average + weight * sample
                        average /= slack + weight
                    slack = 1.0
                smoothed[pos] = average
            elif usable:
                average = sample
                slack = 1.0
                warmed = True
                smoothed[pos] = average
            else:
                smoothed[pos] = np.nan
        return smoothed

    @property
    def name(self) -> str:
        return "atr"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Compute ATR from the high/low/close columns.

        Args:
            data: Frame exposing ``"high"``, ``"low"`` and ``"close"``.

        Returns:
            Series of ATR readings; the first ``period`` rows are NaN.
        """
        highs = data["high"].to_numpy(dtype="float64")
        lows = data["low"].to_numpy(dtype="float64")
        closes = data["close"].to_numpy(dtype="float64")

        # Bar-to-bar true range, seeded with a plain high-low span. Row-wise
        # max ignores missing components (pandas' row-max semantics), so a
        # NaN in one candidate does not poison the bar.
        prev_close = np.concatenate(([closes[0]], closes[:-1]))
        candidates = np.vstack((highs - lows, np.abs(highs - prev_close), np.abs(lows - prev_close)))
        with np.errstate(invalid="ignore"):
            true_range = np.nanmax(candidates, axis=0)

        atr_values = self._wilder_smooth(true_range, self._period)
        atr_values[: self._period] = np.nan
        return pd.Series(atr_values, index=data.index, name="atr")

"""Exponential moving average via an explicit recursion loop."""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator


def _blend_factor(span: int) -> float:
    """Per-bar weight for a given span, mirroring pandas' own arithmetic.

    pandas converts ``span`` to center-of-mass ((span-1)/2) and derives the
    working weight as ``1 / (1 + com)``. Going through the same two-step
    round-trip keeps float64 results bit-equal to ``ewm(span=..., adjust=False)``
    for every span, whereas the textbook shortcut ``2/(span+1)`` can land one
    unit-in-the-last-place off for certain lengths.
    """
    return 1.0 / (1.0 + (span - 1) / 2.0)


class EMA(Indicator):
    """Exponential moving average seeded from the first observation.

    Follows the TradingView ``ta.ema`` convention (``adjust=False`` in pandas
    terms): the series starts at ``close[0]`` and each bar blends the fresh
    price with the running value using weight ``k = 2 / (period + 1)``:

        EMA_t = k * close_t + (1 - k) * EMA_{t-1}

    Rows before the window fills are reported as NaN.
    """

    def _carry_forward(self, observations: np.ndarray, weight: float) -> np.ndarray:
        """Blend successive quotes into one running value.

        Bit-matched to ``ewm(adjust=False)``: the residual weight left from
        the last quote decays once per row — missing rows included — an
        incoming quote identical to the running value bypasses the update
        (pandas declines to divide by the weight sum when nothing would
        change), and the weight ledger returns to 1 after every observed
        row.
        """
        if observations.size == 0:
            return observations.copy()
        fading = 1.0 - weight
        blended = np.empty_like(observations)
        running = observations[0]
        seeded = running == running
        blended[0] = running
        residue = 1.0
        for pos in range(1, len(observations)):
            quote = observations[pos]
            valid = quote == quote
            if seeded:
                residue *= fading
                if valid:
                    if running != quote:
                        running = residue * running + weight * quote
                        running /= residue + weight
                    residue = 1.0
                blended[pos] = running
            elif valid:
                running = quote
                residue = 1.0
                seeded = True
                blended[pos] = running
            else:
                blended[pos] = np.nan
        return blended

    @property
    def name(self) -> str:
        return "ema"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Run the EMA recursion over the close column.

        Args:
            data: Frame containing a ``"close"`` column.

        Returns:
            Series of EMA values; the first ``period - 1`` rows are NaN.
        """
        closes = data["close"].to_numpy(dtype="float64")
        out = self._carry_forward(closes, _blend_factor(self._period))
        out[: self._period - 1] = np.nan
        return pd.Series(out, index=data.index, name="ema")

"""Divergence — price/oscillator divergence scoring (A5).

Divergence detection with two gates:
1. State filter: only score when the oscillator is in a trend regime
   (> 50 for bearish divergence, < 50 for bullish), so signals from the
   middle/ranging zone are skipped.
2. Strength scoring: the score magnitude is the oscillator swing between the
   two pivots, normalized to the oscillator's 0-100 range.
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.indicators.base import Indicator


def _consecutive(pivots: list[tuple[int, float, float]]) -> list[tuple[tuple, tuple]]:
    """Pair each pivot with the next same-direction pivot."""
    return list(zip(pivots, pivots[1:]))


class Divergence(Indicator):
    """Divergence scorer over price pivots.

    A pivot is a bar whose close is the local max/min within ``pivot`` bars on
    each side (window size ``2 * pivot + 1``). At each pair of consecutive
    same-direction pivots:

    - bearish divergence: price makes a higher high while the oscillator makes
      a lower high (both pivots above 50) -> negative score.
    - bullish divergence: price makes a lower low while the oscillator makes a
      higher low (both pivots below 50) -> positive score.

    The oscillator defaults to RSI(close, ``period``); tests may inject a
    precomputed oscillator via a ``"oscillator"`` column in the DataFrame.
    """

    def __init__(self, period: int = 14, pivot: int = 4, min_strength: float = 0.0) -> None:
        super().__init__(period)
        if pivot <= 0:
            raise ValueError(f"pivot must be positive, got {pivot}")
        if not 0.0 <= min_strength <= 1.0:
            raise ValueError(f"min_strength must be in [0, 1], got {min_strength}")
        self._pivot = pivot
        self._min_strength = min_strength

    @property
    def name(self) -> str:
        return "divergence"

    @property
    def pivot(self) -> int:
        """Half-window for pivot detection (each side)."""
        return self._pivot

    @property
    def min_strength(self) -> float:
        """Minimum score magnitude to emit a nonzero signal."""
        return self._min_strength

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Calculate divergence scores.

        Args:
            data: DataFrame with ``"close"`` (and optionally ``"oscillator"``)
                  columns.

        Returns:
            Series of signed divergence scores aligned with the input index:
            positive for bullish divergence, negative for bearish, 0 for bars
            with no detected divergence, NaN before the first pivot pair.
        """
        close = data["close"]

        if "oscillator" in data.columns:
            osc = data["oscillator"]
        else:
            from selene_space_strategy.indicators.rsi import RSI

            osc = RSI(period=self._period).calculate(data)

        scores = pd.Series(0.0, index=data.index)

        high_pivots: list[tuple[int, float, float]] = []
        low_pivots: list[tuple[int, float, float]] = []
        p = self._pivot

        for i in range(p, len(close) - p):
            win = close.iloc[i - p : i + p + 1]
            if self._is_local_extreme(close.iloc[i], win):
                high_pivots.append((i, float(close.iloc[i]), float(osc.iloc[i])))
            if self._is_local_extreme(close.iloc[i], win, low=True):
                low_pivots.append((i, float(close.iloc[i]), float(osc.iloc[i])))

        for (idx1, price1, osc1), (idx2, price2, osc2) in _consecutive(high_pivots):
            bearish = price2 > price1 and osc2 < osc1 and osc1 > 50.0 and osc2 > 50.0
            if bearish:
                strength = min(1.0, (osc1 - osc2) / 100.0)
                if strength >= self._min_strength:
                    scores.iloc[idx2] = -strength

        for (idx1, price1, osc1), (idx2, price2, osc2) in _consecutive(low_pivots):
            bullish = price2 < price1 and osc2 > osc1 and osc1 < 50.0 and osc2 < 50.0
            if bullish:
                strength = min(1.0, (osc2 - osc1) / 100.0)
                if strength >= self._min_strength:
                    scores.iloc[idx2] = strength

        # NaN before the first pivot window is full
        scores.iloc[: p] = float("nan")
        return scores

    @staticmethod
    def _is_local_extreme(value: float, window: pd.Series, low: bool = False) -> bool:
        """True if ``value`` is the window's max (high) / min (low) pivot."""
        if low:
            return value == window.min() and (window.min() < window.max())
        return value == window.max() and (window.max() > window.min())

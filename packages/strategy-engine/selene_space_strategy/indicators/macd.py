"""MACD — momentum from the gap between two EMAs."""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator


def _blend_weight(span: int) -> float:
    """Working weight for an exponential span, matching pandas bit-for-bit.

    pandas routes ``span`` through its center-of-mass form —
    ``com = (span-1)/2`` then ``weight = 1/(1+com)`` — and the round-trip
    shifts the weight by a unit in the last place for some spans. Taking the
    same route keeps the recursion below exactly aligned with
    ``ewm(span=span, adjust=False)``.
    """
    return 1.0 / (1.0 + (span - 1) / 2.0)


class MACDResult:
    """Carrier for the trio of MACD outputs.

    ``macd_line`` is the fast/slow EMA spread, ``signal_line`` an EMA
    laid over that spread, and ``histogram`` their running difference.
    """

    __slots__ = ("histogram", "macd_line", "signal_line")

    def __init__(self, macd_line: pd.Series, signal_line: pd.Series, histogram: pd.Series) -> None:
        self.macd_line = macd_line
        self.signal_line = signal_line
        self.histogram = histogram


class MACD(Indicator):
    """Appel's moving-average convergence/divergence.

    Defaults follow Appel's original 12/26/9 layout. The construction
    chains three exponential averages: one at ``fast_period``, one at
    the slower base window, and a third smoothing the difference of the
    first two. Seeding starts at bar zero and each step folds in
    ``2/(length+1)`` of the new close — Pine-style ``adjust=False``
    behaviour. A fast window at or above the slow one is rejected.
    """

    _DEFAULT_FAST = 12
    _DEFAULT_SIGNAL = 9

    def __init__(
        self,
        period: int = 26,
        fast_period: int = _DEFAULT_FAST,
        signal_period: int = _DEFAULT_SIGNAL,
    ) -> None:
        super().__init__(period)
        if fast_period <= 0:
            raise ValueError(f"fast_period must be positive, got {fast_period}")
        if signal_period <= 0:
            raise ValueError(f"signal_period must be positive, got {signal_period}")
        if fast_period >= period:
            raise ValueError(f"fast_period ({fast_period}) must be less than slow period ({period})")
        self._fast_period = fast_period
        self._signal_period = signal_period

    @property
    def name(self) -> str:
        return "macd"

    @property
    def fast_period(self) -> int:
        """Length of the short EMA leg."""
        return self._fast_period

    @property
    def signal_period(self) -> int:
        """Length of the signal-line smoothing."""
        return self._signal_period

    def _exponential_track(self, source: np.ndarray, length: int) -> np.ndarray:
        """Walk the span-weighted average one bar at a time.

        Reproduces ``ewm(span=length, adjust=False)`` down to the last bit,
        quirks included: the carried weight ages on every row — holes age it
        too, since ``ignore_na`` stays off — a fresh price equal to the
        running value skips the blend entirely rather than dividing by the
        weight sum, and each observed row resets the weight ledger to 1.
        """
        if source.size == 0:
            return source.copy()
        weight = _blend_weight(length)
        aging = 1.0 - weight
        track = np.empty_like(source)
        running = source[0]
        alive = running == running
        track[0] = running
        ledger = 1.0
        for pos in range(1, len(source)):
            fresh = source[pos]
            landed = fresh == fresh
            if alive:
                ledger *= aging
                if landed:
                    if running != fresh:
                        running = ledger * running + weight * fresh
                        running /= ledger + weight
                    ledger = 1.0
                track[pos] = running
            elif landed:
                running = fresh
                ledger = 1.0
                alive = True
                track[pos] = running
            else:
                track[pos] = np.nan
        return track

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Return only the fast-minus-slow spread for a frame with ``"close"``."""
        return self.calculate_full(data).macd_line

    def calculate_full(self, data: pd.DataFrame) -> MACDResult:
        """Compute spread, its signal and the histogram together.

        Input needs a ``"close"`` column. All three returned series stay
        NaN until the slow window has fully elapsed.
        """
        idx = data.index
        closes = data["close"].to_numpy(dtype="float64")

        quick_leg = pd.Series(self._exponential_track(closes, self._fast_period), index=idx)
        slow_leg = pd.Series(self._exponential_track(closes, self._period), index=idx)
        spread = quick_leg - slow_leg

        trigger = pd.Series(
            self._exponential_track(spread.to_numpy(dtype="float64"), self._signal_period),
            index=idx,
        )
        bars = spread - trigger

        # Blank everything ahead of the first complete slow window.
        head = slice(None, self._period - 1)
        spread.iloc[head] = np.nan
        trigger.iloc[head] = np.nan
        bars.iloc[head] = np.nan

        return MACDResult(macd_line=spread, signal_line=trigger, histogram=bars)

    def __repr__(self) -> str:
        return f"MACD(fast={self._fast_period}, slow={self._period}, signal={self._signal_period})"

"""Premium / discount dealing-range detector (ICT).

ICT splits every dealing range into three thirds and treats them as
*positioning*, not as direction:

    premium   (upper third)  — sell zone; longs taken here are chasing
    equilibrium (middle)     — the 50% level; the reference price
    discount  (lower third)  — buy zone; shorts taken here are chasing

The range itself is the last confirmed swing high to the last confirmed
swing low (a leg), which is exactly what
:class:`~selene_space_strategy.indicators.ict.MarketStructure` already
exposes through :meth:`~selene_space_strategy.indicators.ict.MarketStructure.get_swings`.

Signal encoding follows the same incremental-directional convention as the
rest of the ICT family:

    +1  on a bar whose close enters the discount third  (long-friendly)
    -1  on a bar whose close enters the premium third   (short-friendly)
     0  on a bar inside equilibrium, or on a bar that stays in the third
        it was already in (level, not event — the third is a *state*)

Being in the discount third persists, so a strategy pairs +1 with a
structure trigger. Emitting +1 on every discount bar (rather than only on
the crossing bar) is deliberate: an FVG print three bars into a discount
zone is still a discount-context entry.

Causality: the range uses only swings that were *confirmed* at or before
the current bar, mirroring :class:`MarketStructure`'s own rule that a swing
is knowable only once its full right radius has elapsed. A frame that cannot
supply swings yields NaN, never a guess.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator
from selene_space_strategy.indicators.ict import MarketStructure


def _zones(index: pd.Index) -> pd.Series:
    """Float series pre-filled with NaN (warm-up contract)."""
    return pd.Series(np.nan, index=index, dtype=float)


class PremiumDiscount(Indicator):
    """Third-of-range positioning series (+1 discount / -1 premium / 0 mid).

    ``period`` is the fractal radius forwarded to the internal
    :class:`MarketStructure` detector: a swing needs ``period`` lower highs
    (lows) on each side before it counts. ``premium_threshold`` and
    ``discount_threshold`` are the range fractions that bound each zone. The
    ICT defaults are thirds, so premium starts at 2/3 and discount ends at 1/3.
    """

    def __init__(
        self,
        period: int = 10,
        premium_threshold: float = 0.6667,
        discount_threshold: float = 0.3333,
    ) -> None:
        """``period`` sizes the swing radius; the two thresholds are fractions
        of the dealing range measured from its low, and must satisfy
        ``discount_threshold < premium_threshold``."""
        super().__init__(period)
        if not 0.0 < discount_threshold < premium_threshold < 1.0:
            raise ValueError(
                "Thresholds must satisfy 0 < discount_threshold < premium_threshold < 1, "
                f"got {discount_threshold} / {premium_threshold}."
            )
        self._premium = float(premium_threshold)
        self._discount = float(discount_threshold)
        self._latest_range: dict | None = None

    @property
    def name(self) -> str:
        return "premium_discount"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """+1 in the discount third, -1 in the premium third, 0 in between.

        Bars before two swing highs and two swing lows are confirmed stay NaN:
        without both endpoints there is no range to divide.
        """
        structure = MarketStructure(period=self._period)
        structure.calculate(data)
        swings = structure.get_swings()

        highs = [row for row in swings if row["kind"] == "high"]
        lows = [row for row in swings if row["kind"] == "low"]

        signal = _zones(data.index)
        self._latest_range = None
        closes = data["close"]

        # A swing at index k is only knowable from k + period onwards.
        radius = self._period
        h_ptr = 0
        l_ptr = 0
        last_high: tuple[int, float] | None = None
        last_low: tuple[int, float] | None = None

        for i in range(len(data)):
            while h_ptr < len(highs) and highs[h_ptr]["index"] + radius <= i:
                last_high = (highs[h_ptr]["index"], highs[h_ptr]["price"])
                h_ptr += 1
            while l_ptr < len(lows) and lows[l_ptr]["index"] + radius <= i:
                last_low = (lows[l_ptr]["index"], lows[l_ptr]["price"])
                l_ptr += 1

            if last_high is None or last_low is None:
                continue

            high_price = last_high[1]
            low_price = last_low[1]
            span = high_price - low_price
            if span <= 0:
                # Inverted or degenerate range. This is "no reading", not
                # "equilibrium": 0.0 would satisfy a ``>= 0`` exit rule and
                # close a position on unusable data, and the runtime's NaN
                # gate is the only thing that can express that distinction.
                signal.iloc[i] = np.nan
                self._latest_range = None
                continue

            position = (float(closes.iloc[i]) - low_price) / span
            if position >= self._premium:
                signal.iloc[i] = -1.0
            elif position <= self._discount:
                signal.iloc[i] = 1.0
            else:
                signal.iloc[i] = 0.0

            self._latest_range = {
                "bar_index": i,
                "low": low_price,
                "high": high_price,
                "equilibrium": (low_price + high_price) / 2,
                "premium_from": low_price + span * self._premium,
                "discount_to": low_price + span * self._discount,
            }

        return signal

    def get_latest_range(self) -> dict | None:
        """The dealing range behind the last readable bar, or None.

        None means the most recent bar had no usable range (still warming up,
        or a degenerate/inverted swing pair); it never points at an older bar.
        """
        return self._latest_range

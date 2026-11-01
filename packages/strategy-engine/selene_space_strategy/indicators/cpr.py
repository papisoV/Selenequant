"""CPR — Central Pivot Range indicator for intraday support/resistance.

Classic pivot calculation using previous period's High-Low-Close.
Referenced across 10+ articles in laoyulaoyu research as a core
professional intraday trading tool.

Calculation:
    Pivot = (H + L + C) / 3
    R1 = 2*Pivot - L     S1 = 2*Pivot - H
    R2 = Pivot + (H-L)   S2 = Pivot - (H-L)
    R3 = H + 2*(Pivot-L) S3 = L - 2*(H-Pivot)
    BC = (H + L) / 2     TC = Pivot - BC + Pivot
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from selene_space_strategy.indicators.base import Indicator


@dataclass
class CPRLevels:
    """All CPR pivot levels for a single period."""

    pivot: float
    bc: float  # Bottom Central
    tc: float  # Top Central
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float


class CPR(Indicator):
    """Central Pivot Range indicator.

    Computes classic pivot levels from the previous period's HLC.
    For intraday use, feed daily HLC data; for swing, feed weekly.

    The CPR width (TC - BC) indicates expected range:
    - Narrow CPR → range-bound day, fade moves
    - Wide CPR → trending day, follow direction
    """

    def __init__(self, period: int = 1) -> None:
        """Initialize CPR indicator.

        Args:
            period: Number of prior bars to use for HLC (1 = previous bar only).
        """
        super().__init__(period)
        self._levels: list[CPRLevels] = []

    @property
    def name(self) -> str:
        return "cpr"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Calculate CPR pivot values as a Series.

        Args:
            data: DataFrame with 'high', 'low', 'close' columns.

        Returns:
            Series of pivot values (central line).
        """
        high = data["high"]
        low = data["low"]
        close = data["close"]

        result = pd.Series(float("nan"), index=data.index)
        self._levels = []

        for i in range(self._period, len(data)):
            h = high.iloc[i - self._period : i].max()
            l = low.iloc[i - self._period : i].min()
            c = close.iloc[i - 1]

            pivot = (h + l + c) / 3.0
            bc = (h + l) / 2.0
            tc = pivot - bc + pivot

            r1 = 2.0 * pivot - l
            s1 = 2.0 * pivot - h
            r2 = pivot + (h - l)
            s2 = pivot - (h - l)
            r3 = h + 2.0 * (pivot - l)
            s3 = l - 2.0 * (h - pivot)

            levels = CPRLevels(
                pivot=pivot, bc=bc, tc=tc,
                r1=r1, r2=r2, r3=r3,
                s1=s1, s2=s2, s3=s3,
            )
            self._levels.append(levels)
            result.iloc[i] = pivot

        return result

    def calculate_full(self, data: pd.DataFrame) -> list[CPRLevels]:
        """Calculate all CPR levels.

        Args:
            data: DataFrame with 'high', 'low', 'close' columns.

        Returns:
            List of CPRLevels, one per bar (from index period onward).
        """
        self.calculate(data)
        return list(self._levels)

    def get_nearest_levels(self, current_price: float) -> dict[str, float]:
        """Get nearest support and resistance levels to current price.

        Args:
            current_price: Current market price.

        Returns:
            Dict with nearest_support, nearest_resistance, pivot, cpr_width.
        """
        if not self._levels:
            return {}

        latest = self._levels[-1]
        supports = [latest.s1, latest.s2, latest.s3, latest.bc]
        resistances = [latest.r1, latest.r2, latest.r3, latest.tc]

        below = [s for s in supports if s < current_price]
        above = [r for r in resistances if r > current_price]

        nearest_support = max(below) if below else latest.s3
        nearest_resistance = min(above) if above else latest.r3

        return {
            "nearest_support": nearest_support,
            "nearest_resistance": nearest_resistance,
            "pivot": latest.pivot,
            "cpr_width": latest.tc - latest.bc,
        }

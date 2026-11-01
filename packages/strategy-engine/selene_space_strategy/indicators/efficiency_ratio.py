"""ER — Efficiency Ratio (Kaufman's noise/signal classifier)."""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.indicators.base import Indicator


class EfficiencyRatio(Indicator):
    """Efficiency Ratio (Kaufman).

    Net price displacement over the period divided by the total path length:

        ER = |close - close[n]| / sum(|close[i] - close[i-1]|, i in n)

    Bounded to [0, 1]:
    - ER ~ 1  -> clean directional move (little noise), trend-following regime
    - ER ~ 0  -> heavy churn (net displacement near zero), ranging regime

    Used by the ER state machine (A4) to switch between trend-following and
    mean-reversion behavior.
    """

    def __init__(self, period: int = 14) -> None:
        super().__init__(period)

    @property
    def name(self) -> str:
        return "efficiency_ratio"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Calculate the Efficiency Ratio.

        Args:
            data: DataFrame with a ``"close"`` column.

        Returns:
            Series of ER values in [0, 1], aligned with the input index.
            First ``period`` values are NaN (needs period+1 bars for a delta).
        """
        close = data["close"]

        net_move = close.diff(self._period).abs()
        total_path = close.diff().abs().rolling(self._period).sum()

        er = net_move / total_path
        er.iloc[: self._period] = float("nan")
        return er

"""Funding rate indicators — read derivatives data from merged DataFrame.

These indicators read from columns populated by
``compute_derivatives_factors()`` or ``load_4h_1d_deriv()``.
When the columns are missing, they report an all-NaN series with a warning:
0.0 is a *semantically valid* funding rate, so returning it would make
"derivatives data never loaded" indistinguishable from "funding is flat".

Usage in strategy templates:
    entry_rules:
      long:
        - indicator: funding_rate
          params: {period: 1}
          operator: greater_than
          comparator: 0.0003
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator

logger = logging.getLogger(__name__)


class FundingRateIndicator(Indicator):
    """Funding rate indicator reading from merged derivatives data.

    Reads the ``FUNDING_RATE`` column from the DataFrame. This column is
    populated by ``compute_derivatives_factors()`` in pandas_bridge.py or
    by ``load_4h_1d_deriv()`` in shared_backtest.py.

    Returns the raw funding rate value (e.g. 0.0001 = 0.01%).
    """

    def __init__(self, period: int = 1) -> None:
        super().__init__(period)

    @property
    def name(self) -> str:
        return "funding_rate"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Read funding rate from DataFrame.

        Args:
            data: DataFrame with optional ``"FUNDING_RATE"`` column.

        Returns:
            Series of funding rate values, or all-NaN when the column is
            absent (so the caller's warm-up gate skips the bar rather than
            reading a fabricated neutral rate).
        """
        if "FUNDING_RATE" not in data.columns:
            logger.warning(
                "FUNDING_RATE column not found in DataFrame. "
                "Load derivatives data via load_4h_1d_deriv() before using funding_rate indicator.",
            )
            return pd.Series(np.nan, index=data.index, name="funding_rate")
        return data["FUNDING_RATE"].fillna(0.0)


class FundingRateZScore(Indicator):
    """Funding rate Z-score indicator.

    Reads the ``FUNDING_RATE_Z`` column — the funding rate normalized
    by its rolling mean and standard deviation. Useful for detecting
    anomalously high/low funding rates.

    Z > 2: funding rate is unusually high (strong short bias).
    Z < -2: funding rate is unusually low (strong long bias).
    """

    def __init__(self, period: int = 1) -> None:
        super().__init__(period)

    @property
    def name(self) -> str:
        return "funding_rate_z"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Read funding rate Z-score from DataFrame.

        Args:
            data: DataFrame with optional ``"FUNDING_RATE_Z"`` column.

        Returns:
            Series of Z-score values, or all-NaN when the column is absent
            (so the caller's warm-up gate skips the bar rather than reading
            a fabricated neutral score).
        """
        if "FUNDING_RATE_Z" not in data.columns:
            logger.warning(
                "FUNDING_RATE_Z column not found in DataFrame. "
                "Load derivatives data via load_4h_1d_deriv() before using funding_rate_z indicator.",
            )
            return pd.Series(np.nan, index=data.index, name="funding_rate_z")
        return data["FUNDING_RATE_Z"].fillna(0.0)

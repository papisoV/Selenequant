"""Close — raw closing price as an indicator.

Allows strategy YAML to reference ``indicator: close`` directly when
comparing price against another indicator (e.g. close < sma - 2*atr).
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.indicators.base import Indicator


class Close(Indicator):
    """Raw closing price.

    Returns ``data["close"]`` unchanged. Period is accepted to satisfy
    the Indicator base class contract but has no effect on the output.
    """

    @property
    def name(self) -> str:
        return "close"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return data["close"]

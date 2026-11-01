"""Flow-of-funds indicators: volume folded into directional judgement.

Four readings share one skeleton — turn OHLCV bars into a per-bar flow
signal, then aggregate it. The public surface is four standalone
:class:`Indicator` subclasses (OBV, CMF, MFI, Force Index) so registry
lookups and isinstance checks keep working; internally each delegates
to a small private engine to avoid four copies of the same plumbing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator


def _nonzero_mask(values: pd.Series) -> pd.Series:
    """Keep only non-zero cells; zeros become NaN for safe division."""
    return values.mask(values == 0)


def _tag_series(values: pd.Series, label: str) -> pd.Series:
    out = values.copy()
    out.name = label
    return out


class OnBalanceVolume(Indicator):
    """Running total of volume signed by bar direction.

    An up bar adds its volume, a down bar subtracts it, and an unchanged
    close contributes nothing. OBV climbing alongside price supports the
    trend; OBV flat or falling while price rises suggests divergence.
    """

    def __init__(self) -> None:
        super().__init__(period=1)

    @property
    def name(self) -> str:
        return "obv"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Fold direction-signed volume into one accumulator column."""
        moves = data["close"].diff().to_numpy()
        votes = pd.Series(
            np.select([moves > 0.0, moves < 0.0], [1.0, -1.0], default=0.0),
            index=data.index,
        )
        return _tag_series(votes.mul(data["volume"]).cumsum(), self.name)


class ChaikinMoneyFlow(Indicator):
    """Volume-share of close-location pressure averaged over N bars.

    Each bar's money-flow volume places the close inside the high-low
    span via ((C-L)-(H-C))/(H-L); CMF divides the span-weighted flow sum
    by the raw volume sum over ``period`` bars. Beyond +0.25 reads as
    accumulation, beyond -0.25 as distribution.
    """

    def __init__(self, period: int = 20) -> None:
        super().__init__(period=period)

    @property
    def name(self) -> str:
        return "cmf"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Compute CMF; collapsed ranges and warm-up windows read 0.0."""
        span = _nonzero_mask(data["high"] - data["low"])
        placement = ((data["close"] - data["low"]) - (data["high"] - data["close"])) / span
        weighted_flow = placement.fillna(0.0).mul(data["volume"])

        flow_total = weighted_flow.rolling(window=self.period).sum()
        volume_total = data["volume"].rolling(window=self.period).sum()
        return _tag_series((flow_total / _nonzero_mask(volume_total)).fillna(0.0), self.name)


class MoneyFlowIndex(Indicator):
    """Volume-weighted RSI analogue bounded to 0-100.

    Money flow uses typical price (H+L+C)/3 times volume, pooled into
    up-day and down-day buckets over ``period`` bars and mapped through
    ``100 - 100/(1+ratio)``. Above 80 flags overbought, below 20 oversold;
    warm-up cells sit at the neutral midpoint 50.
    """

    _NEUTRAL = 50.0

    def __init__(self, period: int = 14) -> None:
        super().__init__(period=period)

    @property
    def name(self) -> str:
        return "mfi"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Compute MFI from typical-price money-flow pools."""
        typical_price = (data["high"] + data["low"] + data["close"]) / 3.0
        money = typical_price * data["volume"]
        drift = typical_price.diff()

        gains_pool = money.where(drift > 0.0, 0.0).rolling(window=self.period).sum()
        losses_pool = money.where(drift < 0.0, 0.0).rolling(window=self.period).sum()

        ratio = (gains_pool / _nonzero_mask(losses_pool)).fillna(0.0)
        scaled = 100.0 - 100.0 / (1.0 + ratio)
        return _tag_series(scaled.fillna(self._NEUTRAL), self.name)


class ForceIndex(Indicator):
    """EMA-smoothed thrust of price change times traded volume.

    Raw force per bar is ``Δclose · volume``; smoothing span defaults to
    13 (span 1 disables smoothing). Sign gives the side of the pressure,
    magnitude its conviction.
    """

    def __init__(self, period: int = 13) -> None:
        super().__init__(period=period)

    @property
    def name(self) -> str:
        return "force_index"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Compute the smoothed force series; leading NaNs collapse to 0.0."""
        raw_thrust = data["close"].diff().mul(data["volume"])
        if self.period > 1:
            smoothed = raw_thrust.ewm(span=self.period, adjust=False).mean()
        else:
            smoothed = raw_thrust
        return _tag_series(smoothed.fillna(0.0), self.name)

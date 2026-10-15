"""Mean-reversion diagnostics: Hurst exponent, rolling Z-score, OU half-life.

These three readings answer one question from different angles — "does
this series snap back to its average, and how fast?" The Hurst exponent
summarizes long-range persistence, the Z-score measures the current
distance from the mean in sigma units, and the half-life estimates how
many bars a deviation takes to decay by half under an AR(1) model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator


def _flat_series(frame: pd.DataFrame, fill: float, label: str) -> pd.Series:
    """Broadcast a constant diagnostic across the input index."""
    return pd.Series([fill] * len(frame), index=frame.index, name=label)


class HurstExponent(Indicator):
    """Rescaled-range (R/S) estimate of the Hurst exponent.

    Interpretation:
    - H below 0.5: anti-persistent, mean-reverting behaviour
    - H near 0.5: geometric random walk
    - H above 0.5: persistent, trending behaviour

    The estimate regresses log(R/S) against log(lag); too-short inputs,
    or degenerate chunks with zero dispersion, fall back to the neutral
    random-walk value 0.5. The result is clamped to [0, 1].
    """

    def __init__(self, max_lag: int = 20) -> None:
        super().__init__(period=max_lag)
        self.max_lag = max_lag

    @property
    def name(self) -> str:
        return "hurst"

    def _rescaled_range(self, returns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Average R/S statistic per lag together with the lags used."""
        usable_lags = range(2, min(self.max_lag + 1, (returns.shape[0] + 1) // 2))
        lag_axis = []
        rs_means = []
        for lag in usable_lags:
            pieces = len(returns) // lag
            if pieces < 1:
                continue
            segments = np.array_split(returns[: pieces * lag], pieces)
            stats = []
            for seg in segments:
                walk = np.cumsum(seg - seg.mean())
                span = float(walk.max() - walk.min())
                scale = float(seg.std())
                if scale > 0:
                    stats.append(span / scale)
            if stats:
                lag_axis.append(lag)
                rs_means.append(sum(stats) / len(stats))
        return np.asarray(lag_axis, dtype=float), np.asarray(rs_means, dtype=float)

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Estimate H over the whole frame and broadcast it per bar."""
        closes = data["close"].to_numpy(dtype=float)
        total = closes.shape[0]

        if total < self.max_lag + 1:
            return _flat_series(data, 0.5, self.name)

        log_returns = np.diff(np.log(closes))
        lag_axis, rs_curve = self._rescaled_range(log_returns)

        if rs_curve.size < 2:
            return _flat_series(data, 0.5, self.name)

        slope, _intercept = np.polyfit(np.log(lag_axis), np.log(rs_curve), 1)
        exponent = float(np.clip(slope, 0.0, 1.0))
        return _flat_series(data, exponent, self.name)


class ZScoreIndicator(Indicator):
    """Rolling standardized distance from the moving mean.

    z = (close - rolling_mean) / rolling_std over ``window`` bars.
    Large |z| flags stretched conditions; sign encodes direction.
    Warm-up cells (insufficient history or zero dispersion) read 0.0.
    """

    def __init__(self, window: int = 20) -> None:
        super().__init__(period=window)
        self.window = window

    @property
    def name(self) -> str:
        return "zscore"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Compute the rolling Z-score of closes.

        Args:
            data: DataFrame carrying a 'close' column.

        Returns:
            Series named 'zscore' aligned to the input index.
        """
        closes = data["close"]
        baseline = closes.rolling(window=self.window).mean()
        dispersion = closes.rolling(window=self.window).std()

        stretched = (closes - baseline) / dispersion.where(dispersion != 0)
        return stretched.fillna(0.0).rename(self.name)


class HalfLifeIndicator(Indicator):
    """Half-life of mean reversion under an Ornstein-Uhlenbeck model.

    Fits the AR(1) recursion ``Δlog(p_t) = φ · log(p_{t-1}) + ε`` and
    converts the persistence coefficient via ``-ln(2)/ln(φ)``:

    - small positive half-life: fast snap-back, tradable reversion
    - large half-life: sluggish reversion
    - φ outside (0, 1): no usable reversion → infinite half-life

    Frames shorter than 10 rows also report infinity.
    """

    _MIN_BARS = 10

    def __init__(self) -> None:
        super().__init__(period=1)

    @property
    def name(self) -> str:
        return "half_life"

    def _ar1_persistence(self, logged: np.ndarray) -> float:
        """Least-squares slope of the log-return on its lagged log-level."""
        steps = np.diff(logged)
        levels = logged[:-1]
        variance = float(levels.var())
        if variance == 0.0:
            return float("nan")
        return float(np.cov(steps, levels)[0, 1]) / variance

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Estimate the decay half-life and broadcast it across the frame."""
        closes = data["close"].to_numpy(dtype=float)
        total = closes.shape[0]

        if total < self._MIN_BARS:
            return _flat_series(data, float("inf"), self.name)

        phi = self._ar1_persistence(np.log(closes))
        if not np.isnan(phi) and 0.0 < phi < 1.0:
            decay = float(-np.log(2.0) / np.log(phi))
        else:
            decay = float("inf")

        return _flat_series(data, decay, self.name)

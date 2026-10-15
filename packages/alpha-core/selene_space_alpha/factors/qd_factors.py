"""QD-absorption B3 factors — CHOPPINESS / ELDER_BULL / ELDER_BEAR (cleanroom).

Thought-level absorption of three classic technical indicators observed in the
user's QuantDinger factor registry (Apache-2.0 source, whitelist
``registry.py:206-208``): choppiness (period 14) and elder ray bull/bear
(period 13). The implementations below are written from the public textbook
definitions of the indicators — not from any QuantDinger code — following the
same cleanroom protocol as the grid-engine absorption (commit 2e648c1).

Both are single-asset OHLCV-only factors following the
``awesome_factors.py`` contract: ``compute_*(data: pd.DataFrame) -> pd.Series``
with zero-fill degradation on missing columns, registered into
``ALL_FACTORS`` and exposed as new tokens in the formula vocabulary.

Indicator definitions (textbook):

- Choppiness Index (Dreiss): ``100 * log10(sum(TR, n) / (maxHi - minLo)) / log10(n)``
  — ranges 0..100; high = ranging/choppy market, low = trending. The QD
  registry marks it ``lower_is_bullish`` (trending = bullish evidence).
- Elder Ray (Elder): bull power = ``high - EMA(close, n)``; bear power =
  ``low - EMA(close, n)``. Signed pressure of buyers/sellers around the
  trend baseline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _true_range(data: pd.DataFrame) -> pd.Series:
    """True range: max(h-l, |h-prev_c|, |l-prev_c|), first bar = h-l."""
    high = data["high"]
    low = data["low"]
    prev_close = data["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.fillna(high - low)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def compute_choppiness_14(data: pd.DataFrame) -> pd.Series:
    """Choppiness Index over 14 bars, range 0..100 (higher = choppier)."""
    if not {"high", "low", "close"}.issubset(data.columns):
        return pd.Series(0.0, index=data.index)
    period = 14
    tr_sum = _true_range(data).rolling(window=period, min_periods=period).sum()
    hi = data["high"].rolling(window=period, min_periods=period).max()
    lo = data["low"].rolling(window=period, min_periods=period).min()
    denom = (hi - lo).replace(0.0, np.nan)
    ratio = tr_sum / denom
    ci = 100.0 * np.log10(ratio) / np.log10(period)
    return ci.fillna(0.0)


def compute_elder_bull_13(data: pd.DataFrame) -> pd.Series:
    """Elder Ray bull power: high - EMA(close, 13)."""
    if not {"high", "close"}.issubset(data.columns):
        return pd.Series(0.0, index=data.index)
    ema = _ema(data["close"], 13)
    return (data["high"] - ema).fillna(0.0)


def compute_elder_bear_13(data: pd.DataFrame) -> pd.Series:
    """Elder Ray bear power: low - EMA(close, 13)."""
    if not {"low", "close"}.issubset(data.columns):
        return pd.Series(0.0, index=data.index)
    ema = _ema(data["close"], 13)
    return (data["low"] - ema).fillna(0.0)


QD_FACTORS: dict[str, tuple[object, str]] = {
    "qd_choppiness_14": (compute_choppiness_14, "Choppiness Index (14, 0-100)"),
    "qd_elder_bull_13": (compute_elder_bull_13, "Elder Ray bull power (13)"),
    "qd_elder_bear_13": (compute_elder_bear_13, "Elder Ray bear power (13)"),
}

"""Awesome-systematic-trading factors — single-asset, OHLCV-only.

Distilled from academic-strategy-catalog recipes that are
reproducible from a single asset's open/high/low/close/volume bars (no
fundamentals, no cross-sectional universe). Each factor is a standalone
``compute_aw_*(data: pd.DataFrame) -> pd.Series`` mirroring the pandas_bridge
contract, so they can be registered into ``ALL_FACTORS`` and exposed as new
tokens in the formula vocabulary.

Recipe provenance (strategy -> factor):
  - 52-week high effect      -> AW_52W_HIGH
  - time-series momentum     -> AW_TSMOM_12M / AW_TSMOM_SIGN
  - commodity momentum       -> AW_MOM_6M
  - consistent momentum      -> AW_CONSISTENT_MOM
  - short-term reversal      -> AW_REVERSAL_5D / AW_REVERSAL_10D
  - low volatility           -> AW_LOW_VOL
  - skewness effect          -> AW_SKEW_12M
  - return asymmetry         -> AW_RETURN_ASYMMETRY
  - trend following          -> AW_TREND_MA_SLOPE / AW_TREND_ABOVE_MA
  - turn of the month        -> AW_TURN_OF_MONTH
  - overnight anomaly        -> AW_OVERNIGHT_8H
  - volatility risk premium  -> AW_VOL_RISK_PREMIUM

Windows use calendar approximations (252d/year, 21d/month, 5d/week) and are
anchored to the last bar, so all factors are point-in-time safe for backtests.
Missing columns degrade to a zero Series instead of raising.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# --- helpers ----------------------------------------------------------------


def _ret(data: pd.DataFrame) -> pd.Series:
    """Log or simple return of close; simple return for readability."""
    return data["close"].pct_change().fillna(0.0)


def _month_return(data: pd.DataFrame, months: int) -> pd.Series:
    """Cumulative return over trailing ``months`` (21 trading days each)."""
    window = months * 21
    return data["close"].pct_change(window).fillna(0.0)


def _vol(data: pd.DataFrame, window: int) -> pd.Series:
    """Annualized-ish rolling volatility of daily returns (scaled by sqrt(n))."""
    return _ret(data).rolling(window=window, min_periods=2).std().fillna(0.0)


def _scaled(series: pd.Series) -> pd.Series:
    """Normalize a series into [-1, 1] for use as a token feature."""
    s = series.fillna(0.0)
    span = s.abs().max()
    if span <= 1e-12:
        return s.clip(-1.0, 1.0)
    return (s / span).clip(-1.0, 1.0)


# --- momentum family --------------------------------------------------------


def compute_aw_52w_high(data: pd.DataFrame) -> pd.Series:
    """52-week high proximity: close relative to its trailing 252-day high.

    From ``52-weeks-high-effect-in-stocks.py``: stocks near their 52-week
    high keep outperforming (George-Hwang). Returns 1.0 at the high, decaying
    to lower values as the close falls below it.
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    high = data["close"].rolling(window=252, min_periods=2).max().fillna(0.0)
    prox = data["close"] / high.replace(0.0, np.nan)
    return _scaled(prox.fillna(0.0))


def compute_aw_tsmom_12m(data: pd.DataFrame) -> pd.Series:
    """12-month time-series momentum (sign * magnitude, Moskowitz-Ooi-Pedersen).

    From ``time-series-momentum-effect.py``: long if the trailing 12m return
    is positive, short if negative, scaled by recent volatility. Kept as a
    continuous signed return so the VM can combine it with operators.
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    return _scaled(_month_return(data, 12))


def compute_aw_tsmom_sign(data: pd.DataFrame) -> pd.Series:
    """Pure sign of 12-month time-series momentum: +1 long, -1 short, 0 flat."""
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    return np.sign(_month_return(data, 12))


def compute_aw_mom_6m(data: pd.DataFrame) -> pd.Series:
    """6-month commodity momentum (momentum-effect-in-commodities.py)."""
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    return _scaled(_month_return(data, 6))


def compute_aw_consistent_mom(data: pd.DataFrame) -> pd.Series:
    """Consistent momentum: both the trailing month AND the month before it
    must be positive. From consistent-momentum-strategy.py (top decile in
    both t-7..t-1 and t-6..t-0 periods). Returns 1 when consistent-long,
    -1 when consistent-short, 0 otherwise.
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    m1 = data["close"].pct_change(21).fillna(0.0)
    m2 = data["close"].pct_change(42).fillna(0.0)
    both_pos = (m1 > 0) & (m2 > 0)
    both_neg = (m1 < 0) & (m2 < 0)
    return pd.Series(
        np.where(both_pos, 1.0, np.where(both_neg, -1.0, 0.0)),
        index=data.index,
    )


# --- reversal family --------------------------------------------------------


def compute_aw_reversal_5d(data: pd.DataFrame) -> pd.Series:
    """5-day short-term reversal: negative of the trailing week return.

    From ``short-term-reversal-in-stocks.py``: buy the prior week's losers.
    The negative sign encodes the contrarian direction directly.
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    wk = data["close"].pct_change(5).fillna(0.0)
    return -_scaled(wk)


def compute_aw_reversal_10d(data: pd.DataFrame) -> pd.Series:
    """10-day reversal: negative of the trailing two-week return."""
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    two_week = data["close"].pct_change(10).fillna(0.0)
    return -_scaled(two_week)


# --- volatility family ------------------------------------------------------


def compute_aw_low_vol(data: pd.DataFrame) -> pd.Series:
    """Low-volatility factor: negative of trailing 21d realized vol.

    From ``low-volatility-factor-effect-in-stocks.py``: low-vol assets earn
    higher risk-adjusted returns. Negative vol so higher = less risky.
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    return -_scaled(_vol(data, 21))


def compute_aw_vol_risk_premium(data: pd.DataFrame) -> pd.Series:
    """Volatility risk premium proxy: short-term vol minus long-term vol.

    From ``volatility-risk-premium-effect.py``: shorting straddles harvests
    the premium when realized vol is below implied. Here long-term (252d)
    realized vol stands in for the premium anchor.
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    short_v = _vol(data, 21)
    long_v = _vol(data, 252)
    diff = short_v - long_v
    return _scaled(diff)


def compute_aw_skew_12m(data: pd.DataFrame) -> pd.Series:
    """12-month return skewness (skewness-effect-in-commodities.py).

    Negatively skewed assets are shorted (lottery demand pushes their
    price up); the factor keeps sign so higher = more positively skewed.
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    skew = _ret(data).rolling(window=252, min_periods=21).skew().fillna(0.0)
    return _scaled(skew)


def compute_aw_return_asymmetry(data: pd.DataFrame) -> pd.Series:
    """Return asymmetry measure IE (return-asymmetry-effect-in-commodity-
    futures.py): (#days > mean+2sd) - (#days < mean-2sd) over 260 days.

    Positive = fat right tail (lottery-like), negative = fat left tail.
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    r = _ret(data)
    mean = r.rolling(window=260, min_periods=21).mean()
    sd = r.rolling(window=260, min_periods=21).std()
    up = (r > mean + 2.0 * sd).rolling(window=260).sum().fillna(0.0)
    down = (r < mean - 2.0 * sd).rolling(window=260).sum().fillna(0.0)
    return _scaled(up - down)


# --- trend family -----------------------------------------------------------


def compute_aw_trend_ma_slope(data: pd.DataFrame) -> pd.Series:
    """Trend-following MA slope: normalized change of the 21d MA over 5 days.

    From ``trend-following-effect-in-stocks.py`` / asset-class trend
    following: the trend's slope (not level) drives long/flat/short.
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    ma = data["close"].rolling(window=21, min_periods=2).mean()
    slope = ma - ma.shift(5)
    return _scaled(slope / (ma + 1e-9))


def compute_aw_trend_above_ma(data: pd.DataFrame) -> pd.Series:
    """Trend level: close relative to its 50d moving average.

    Positive when price is above the trend anchor.
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    ma = data["close"].rolling(window=50, min_periods=2).mean()
    ratio = data["close"] / (ma + 1e-9) - 1.0
    return _scaled(ratio)


# --- calendar family --------------------------------------------------------


def compute_aw_turn_of_month(data: pd.DataFrame) -> pd.Series:
    """Turn-of-the-month effect: proximity to month end.

    From ``turn-of-the-month-in-equity-indexes.py``: most returns accrue
    around the month boundary (last day + first 3 days). Encodes +1 for the
    last trading day of the month, +0.5 for the first 3 days, 0 elsewhere.
    Requires a "timestamp" column (epoch ms); degrades to 0 without it.
    """
    if "timestamp" not in data:
        return pd.Series(0.0, index=data.index)
    try:
        ts = pd.to_datetime(data["timestamp"], unit="ms")
    except (TypeError, ValueError):
        return pd.Series(0.0, index=data.index)
    prev_month_end = ts.dt.to_period("M").dt.end_time
    days_to_end = (prev_month_end - ts).dt.days
    next_month_day = ts.dt.day
    prox = np.where(days_to_end == 0, 1.0, 0.0)
    prox = np.where((days_to_end > 0) & (days_to_end <= 3), 0.5, prox)
    prox = np.where(next_month_day <= 3, 0.5, prox)
    return pd.Series(prox, index=data.index)


def compute_aw_overnight_8h(data: pd.DataFrame) -> pd.Series:
    """Overnight anomaly proxy: gap from previous close to today's open.

    From ``market-sentiment-and-an-overnight-anomaly.py`` / BTC intraday
    seasonality: overnight returns behave differently from intraday. On
    daily bars open exists; on bars without it the factor degrades to 0.
    """
    if "open" not in data or "close" not in data:
        return pd.Series(0.0, index=data.index)
    overnight = data["open"] / data["close"].shift(1) - 1.0
    return _scaled(overnight.fillna(0.0))


def compute_aw_mom_acceleration(data: pd.DataFrame) -> pd.Series:
    """Momentum acceleration: 6m momentum minus 3m momentum.

    From ``momentum-factor-and-style-rotation-effect.py``: styles rotate
    between fast and slow regimes; a widening fast-vs-slow gap signals the
    trend is accelerating (positive) or decelerating (negative).
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    m6 = data["close"].pct_change(126).fillna(0.0)
    m3 = data["close"].pct_change(63).fillna(0.0)
    return _scaled(m6 - m3)


def compute_aw_trend_ma_cross(data: pd.DataFrame) -> pd.Series:
    """Trend-following MA crossover: 21d MA minus 50d MA, normalized.

    From ``asset-class-trend-following.py``: the fast/slow MA gap is the
    classic trend signal; positive = bullish alignment.
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    fast = data["close"].rolling(window=21, min_periods=2).mean()
    slow = data["close"].rolling(window=50, min_periods=2).mean()
    cross = (fast - slow) / (slow + 1e-9)
    return _scaled(cross)


def compute_aw_drawdown(data: pd.DataFrame) -> pd.Series:
    """Trailing drawdown from the 252d high (risk regime).

    From the risk-control family (low-vol / trend overlay): deep drawdowns
    precede mean-reversion bounces, while shallow drawdowns mark strong
    trends. Negative = under water.
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    high = data["close"].rolling(window=252, min_periods=2).max()
    dd = data["close"] / high - 1.0
    return _scaled(dd.fillna(0.0))


def compute_aw_vol_of_vol(data: pd.DataFrame) -> pd.Series:
    """Volatility clustering: std of the 21d realized vol over 63d.

    From the volatility-risk-premium family: periods of high vol-of-vol are
    the volatile regimes where risk premia widen.
    """
    if "close" not in data:
        return pd.Series(0.0, index=data.index)
    vol = _vol(data, 21)
    vov = vol.rolling(window=63, min_periods=2).std().fillna(0.0)
    return _scaled(vov)


def compute_aw_volume_trend(data: pd.DataFrame) -> pd.Series:
    """Volume trend: fast (5d) vs slow (21d) average volume ratio.

    From ``rebalancing-premium-in-cryptocurrencies.py``'s liquidity-aware
    weighting: rising relative volume confirms participation in a move.
    """
    if "volume" not in data:
        return pd.Series(0.0, index=data.index)
    fast = data["volume"].rolling(window=5, min_periods=1).mean()
    slow = data["volume"].rolling(window=21, min_periods=1).mean()
    ratio = fast / (slow + 1e-9) - 1.0
    return _scaled(ratio)


# --- registry ---------------------------------------------------------------


AWESOME_FACTORS: dict[str, tuple[object, str]] = {
    "aw_52w_high": (compute_aw_52w_high, "52-week high proximity"),
    "aw_tsmom_12m": (compute_aw_tsmom_12m, "12-month time-series momentum"),
    "aw_tsmom_sign": (compute_aw_tsmom_sign, "Sign of 12-month TSMOM"),
    "aw_mom_6m": (compute_aw_mom_6m, "6-month commodity momentum"),
    "aw_consistent_mom": (compute_aw_consistent_mom, "Consistent momentum (dual month)"),
    "aw_reversal_5d": (compute_aw_reversal_5d, "5-day short-term reversal"),
    "aw_reversal_10d": (compute_aw_reversal_10d, "10-day reversal"),
    "aw_low_vol": (compute_aw_low_vol, "Low-volatility factor"),
    "aw_vol_risk_premium": (compute_aw_vol_risk_premium, "Volatility risk premium proxy"),
    "aw_skew_12m": (compute_aw_skew_12m, "12-month return skewness"),
    "aw_return_asymmetry": (compute_aw_return_asymmetry, "Return asymmetry IE"),
    "aw_trend_ma_slope": (compute_aw_trend_ma_slope, "Trend MA slope"),
    "aw_trend_above_ma": (compute_aw_trend_above_ma, "Close vs 50d MA"),
    "aw_turn_of_month": (compute_aw_turn_of_month, "Turn-of-month proximity"),
    "aw_overnight_8h": (compute_aw_overnight_8h, "Overnight gap anomaly"),
    "aw_mom_acceleration": (compute_aw_mom_acceleration, "Momentum acceleration (6m-3m)"),
    "aw_trend_ma_cross": (compute_aw_trend_ma_cross, "Fast/slow MA crossover"),
    "aw_drawdown": (compute_aw_drawdown, "Trailing drawdown from 252d high"),
    "aw_vol_of_vol": (compute_aw_vol_of_vol, "Volatility of volatility"),
    "aw_volume_trend": (compute_aw_volume_trend, "Fast/slow volume trend"),
}

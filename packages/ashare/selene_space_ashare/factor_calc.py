"""
Factor calculation functions for A-share stocks.

Each function takes a DataFrame of daily klines (columns: date, open, high, low, close, volume)
and returns a Series/DataFrame of factor values indexed by date.
"""

import numpy as np
import pandas as pd


# ─── Momentum Factors ──────────────────────────────────────────────


def momentum(df: pd.DataFrame, window: int = 21) -> pd.Series:
    """Price return over N trading days."""
    return df["close"].pct_change(window)


def high_52w_proximity(df: pd.DataFrame, window: int = 252) -> pd.Series:
    """Proximity to N-day high: close / rolling_max(close)."""
    return df["close"] / df["close"].rolling(window, min_periods=1).max()


# ─── Volatility Factors ────────────────────────────────────────────


def volatility(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Realized volatility: std of daily returns over window."""
    returns = df["close"].pct_change()
    return returns.rolling(window).std() * np.sqrt(252)


def bb_width(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Bollinger Band width: (upper - lower) / middle."""
    ma = df["close"].rolling(window).mean()
    std = df["close"].rolling(window).std()
    return (2 * std) / ma


def atr_ratio(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """ATR(close) / close, normalized true range."""
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window).mean() / df["close"]


# ─── Mean Reversion Factors ────────────────────────────────────────


def price_zscore(df: pd.Series, window: int = 20) -> pd.Series:
    """Z-score of series vs rolling mean."""
    ma = df.rolling(window).mean()
    std = df.rolling(window).std()
    return (df - ma) / std


def ma_deviation(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """(close - MA) / MA, deviation from moving average."""
    ma = df["close"].rolling(window).mean()
    return (df["close"] - ma) / ma


def rsi(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ─── Crossover Factors ────────────────────────────────────────────


def macd_signal(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.Series:
    """MACD line - signal line. Positive = bullish crossover."""
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line - signal_line


def golden_cross(df: pd.DataFrame) -> pd.Series:
    """MA alignment: 1 if MA5 > MA20 > MA60, else 0."""
    ma5 = df["close"].rolling(5).mean()
    ma20 = df["close"].rolling(20).mean()
    ma60 = df["close"].rolling(60).mean()
    return ((ma5 > ma20) & (ma20 > ma60)).astype(int)


# ─── Volume-Price Factors ──────────────────────────────────────────


def volume_zscore(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Z-score of volume vs rolling mean."""
    return price_zscore(df["volume"], window)


def obv_slope(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """OBV slope over window, measures accumulation/distribution."""
    direction = np.sign(df["close"].diff())
    obv = (direction * df["volume"]).cumsum()
    return obv.diff(window) / obv.rolling(window).std()


def volume_climax(
    df: pd.DataFrame, vol_mult: float = 3.0, window: int = 20
) -> pd.Series:
    """1 if volume > vol_mult * avg AND wide range candle, else 0."""
    avg_vol = df["volume"].rolling(window).mean()
    range_pct = (df["high"] - df["low"]) / df["close"]
    avg_range = range_pct.rolling(window).mean()
    return ((df["volume"] > vol_mult * avg_vol) & (range_pct > avg_range * 1.5)).astype(
        int
    )


# ─── Size Factor (panel/cross-section use) ─────────────────────────

SIZE_KLINE_COLS = ["volume", "turnover", "close"]


def derive_size_factor(df: pd.DataFrame) -> pd.Series:
    """SIZE = ln(float market cap), derived purely from kline columns.

    float_shares = volume / (turnover / 100) — kline turnover is in percent
    (0.248 means 0.248%). Anchors verified live: 万科 97.15亿, 茅台 12.49亿,
    平安银行 194亿 (float shares). Resulting market cap is the FLOAT cap
    (tradable), not total cap. Rows with turnover<=0/NaN → NaN (suspended
    days have zero turnover and would divide by zero).
    """
    turnover_pct = pd.to_numeric(df["turnover"], errors="coerce")
    float_shares = df["volume"] * 100.0 / turnover_pct.where(turnover_pct > 0)
    mktcap = float_shares * df["close"]
    return np.log(mktcap.where(mktcap > 0))


# ─── Composite Factor Calculation ──────────────────────────────────

TECHNICAL_CALCULATORS = {
    "MOMENTUM_1M": lambda df: momentum(df, 21),
    "MOMENTUM_3M": lambda df: momentum(df, 63),
    "MOMENTUM_6M": lambda df: momentum(df, 126),
    "MOMENTUM_12M": lambda df: momentum(df, 252),
    "HIGH_52W_PROXIMITY": lambda df: high_52w_proximity(df, 252),
    "VOLATILITY_20D": lambda df: volatility(df, 20),
    "BB_WIDTH": lambda df: bb_width(df, 20),
    "ATR_RATIO": lambda df: atr_ratio(df, 14),
    "PRICE_ZSCORE_20D": lambda df: price_zscore(df["close"], 20),
    "MA_DEVIATION": lambda df: ma_deviation(df, 20),
    "RSI_14D": lambda df: rsi(df, 14),
    "MACD_SIGNAL": lambda df: macd_signal(df),
    "GOLDEN_CROSS": lambda df: golden_cross(df),
    "VOLUME_ZSCORE": lambda df: volume_zscore(df, 20),
    "VOLUME_PRICE_TREND": lambda df: obv_slope(df, 20),
    "VOLUME_CLIMAX": lambda df: volume_climax(df),
}


def calculate_technical_factors(klines: pd.DataFrame) -> pd.DataFrame:
    """Calculate all technical factors for a single stock's kline data.

    Args:
        klines: DataFrame with columns [date, open, high, low, close, volume]

    Returns:
        DataFrame indexed by date with factor columns
    """
    result = {"date": klines["date"].values}

    for name, calc in TECHNICAL_CALCULATORS.items():
        try:
            series = calc(klines)
            result[name] = series.values
        except Exception:
            result[name] = np.nan

    df = pd.DataFrame(result)
    return df


def calculate_factors_batch(
    klines_dict: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Calculate technical factors for multiple stocks.

    Args:
        klines_dict: {symbol: klines_df}

    Returns:
        {symbol: factors_df}
    """
    results = {}
    for sym, klines in klines_dict.items():
        if klines.empty or len(klines) < 60:
            continue
        factors = calculate_technical_factors(klines)
        if not factors.empty:
            results[sym] = factors
    return results

from __future__ import annotations

import math

import pandas as pd


def calculate_indicators(history: pd.DataFrame) -> dict[str, float | int | str | None]:
    data = history.copy()
    close = data["Close"].astype(float)
    high = data["High"].astype(float)
    low = data["Low"].astype(float)
    volume = data["Volume"].astype(float) if "Volume" in data else pd.Series(dtype=float)

    returns = close.pct_change()
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, math.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((loss == 0) & (gain > 0), 100)
    rsi = rsi.mask((gain == 0) & (loss > 0), 0)
    rsi = rsi.mask((gain == 0) & (loss == 0), 50)

    mid = sma20
    std20 = close.rolling(20).std()
    upper = mid + 2 * std20
    lower = mid - 2 * std20

    true_range = pd.concat(
        [
            (high - low),
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = true_range.rolling(14).mean()

    last_close = _last(close)
    support_20 = _last(low.rolling(20).min())
    resistance_20 = _last(high.rolling(20).max())
    volume_ratio = None
    if len(volume.dropna()) >= 20:
        volume_ratio = _safe_div(_last(volume), _last(volume.rolling(20).mean()))

    metrics = {
        "last_close": last_close,
        "sma20": _last(sma20),
        "sma50": _last(sma50),
        "sma200": _last(sma200),
        "rsi14": _last(rsi),
        "macd": _last(macd),
        "macd_signal": _last(macd_signal),
        "bollinger_position": _safe_div(last_close - _last(lower), _last(upper) - _last(lower)),
        "atr14": _last(atr14),
        "atr_pct": _safe_div(_last(atr14), last_close),
        "volatility_20d": _last(returns.rolling(20).std()) * math.sqrt(252) if len(close) >= 21 else None,
        "drawdown_60d": _drawdown(close, 60),
        "return_5d": _period_return(close, 5),
        "return_20d": _period_return(close, 20),
        "return_60d": _period_return(close, 60),
        "support_20d": support_20,
        "resistance_20d": resistance_20,
        "volume_ratio_20d": volume_ratio,
        "rows": int(len(data)),
    }
    return {key: _round(value) for key, value in metrics.items()}


def _last(series: pd.Series) -> float | None:
    values = series.dropna()
    if values.empty:
        return None
    return float(values.iloc[-1])


def _period_return(close: pd.Series, periods: int) -> float | None:
    if len(close.dropna()) <= periods:
        return None
    return _safe_div(float(close.iloc[-1]), float(close.iloc[-periods - 1])) - 1


def _drawdown(close: pd.Series, window: int) -> float | None:
    values = close.dropna().tail(window)
    if values.empty:
        return None
    peak = values.cummax()
    drawdown = values / peak - 1
    return float(drawdown.min())


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    if pd.isna(numerator) or pd.isna(denominator):
        return None
    return float(numerator) / float(denominator)


def _round(value: float | int | str | None) -> float | int | str | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if pd.isna(value) or math.isinf(float(value)):
        return None
    return round(float(value), 6)

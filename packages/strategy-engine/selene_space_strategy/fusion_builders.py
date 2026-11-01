"""Fusion strategy signal builders — actual logic for multi-indicator strategies.

Each function takes a DataFrame with OHLCV data and strategy parameters,
computes the required indicators, and returns a list of Signals.

These implement the "logic" strings defined in factory.py FUSION_STRATEGIES.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


def _make_signal(
    strategy_id: str,
    symbol: str,
    side: OrderSide,
    timestamp: int,
    reason: str,
) -> Signal:
    return Signal(
        strategy_id=strategy_id,
        symbol=symbol,
        side=side,
        strength=1.0,
        timestamp=timestamp,
        reason=reason,
    )


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI indicator."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    # Use exponential moving average after initial SMA
    for i in range(period, len(gain)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute MACD line, signal line, and histogram."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _compute_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Compute ADX indicator."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr = tr.rolling(window=period, min_periods=1).mean()
    plus_di = 100 * (plus_dm.rolling(window=period, min_periods=1).mean() / atr.replace(0, 1e-10))
    minus_di = 100 * (minus_dm.rolling(window=period, min_periods=1).mean() / atr.replace(0, 1e-10))

    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10))
    adx = dx.rolling(window=period, min_periods=1).mean()
    return adx


def _compute_bollinger(
    close: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute Bollinger Bands: middle, upper, lower."""
    middle = close.rolling(window=period, min_periods=1).mean()
    std = close.rolling(window=period, min_periods=1).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return middle, upper, lower


# ---------------------------------------------------------------------------
# Fusion Strategy Implementations
# ---------------------------------------------------------------------------


def rsi_macd_confluence(
    df: pd.DataFrame,
    params: dict[str, Any] | None = None,
) -> list[Signal]:
    """RSI + MACD confluence: both must agree for stronger signal.

    Entry long: RSI oversold AND MACD histogram positive (momentum turning up).
    Entry short: RSI overbought AND MACD histogram negative.
    Exit long: RSI overbought OR MACD histogram negative.
    Exit short: RSI oversold OR MACD histogram positive.
    """
    p = params or {}
    rsi_period = p.get("rsi_period", 14)
    rsi_oversold = p.get("rsi_oversold", 30)
    rsi_overbought = p.get("rsi_overbought", 70)
    macd_fast = p.get("macd_fast", 12)
    macd_slow = p.get("macd_slow", 26)
    macd_signal = p.get("macd_signal", 9)

    rsi = _compute_rsi(df["close"], rsi_period)
    _, _, histogram = _compute_macd(df["close"], macd_fast, macd_slow, macd_signal)

    signals: list[Signal] = []
    position: str = "flat"
    symbol = df.get("symbol", ["BTC/USDT"])
    if isinstance(symbol, pd.Series):
        symbol = symbol.iloc[0] if len(symbol) > 0 else "BTC/USDT"

    for i in range(1, len(df)):
        ts = int(df["timestamp"].iloc[i]) if "timestamp" in df.columns else i * 3600000

        if pd.isna(rsi.iloc[i]) or pd.isna(histogram.iloc[i]):
            continue

        if position == "flat":
            # Long entry: RSI oversold + MACD histogram turning positive
            if rsi.iloc[i] < rsi_oversold and histogram.iloc[i] > 0:
                signals.append(_make_signal(
                    "rsi_macd_confluence", str(symbol), OrderSide.BUY, ts,
                    f"RSI={rsi.iloc[i]:.1f}<{rsi_oversold} + MACD histogram positive",
                ))
                position = "long"
            # Short entry: RSI overbought + MACD histogram turning negative
            elif rsi.iloc[i] > rsi_overbought and histogram.iloc[i] < 0:
                signals.append(_make_signal(
                    "rsi_macd_confluence", str(symbol), OrderSide.SELL, ts,
                    f"RSI={rsi.iloc[i]:.1f}>{rsi_overbought} + MACD histogram negative",
                ))
                position = "short"

        elif position == "long":
            if rsi.iloc[i] > rsi_overbought or histogram.iloc[i] < 0:
                signals.append(_make_signal(
                    "rsi_macd_confluence", str(symbol), OrderSide.SELL, ts,
                    f"Exit long: RSI={rsi.iloc[i]:.1f} or MACD histogram negative",
                ))
                position = "flat"

        elif position == "short":
            if rsi.iloc[i] < rsi_oversold or histogram.iloc[i] > 0:
                signals.append(_make_signal(
                    "rsi_macd_confluence", str(symbol), OrderSide.BUY, ts,
                    f"Exit short: RSI={rsi.iloc[i]:.1f} or MACD histogram positive",
                ))
                position = "flat"

    return signals


def macd_adx_filter(
    df: pd.DataFrame,
    params: dict[str, Any] | None = None,
) -> list[Signal]:
    """MACD + ADX filter: MACD signals only when trend is strong.

    Entry long: MACD crosses above signal AND ADX > threshold.
    Entry short: MACD crosses below signal AND ADX > threshold.
    Exit: MACD opposite crossover.
    """
    p = params or {}
    macd_fast = p.get("macd_fast", 12)
    macd_slow = p.get("macd_slow", 26)
    macd_signal = p.get("macd_signal", 9)
    adx_period = p.get("adx_period", 14)
    adx_threshold = p.get("adx_threshold", 25)

    macd_line, signal_line, histogram = _compute_macd(df["close"], macd_fast, macd_slow, macd_signal)
    adx = _compute_adx(df["high"], df["low"], df["close"], adx_period)

    signals: list[Signal] = []
    position: str = "flat"
    symbol = df.get("symbol", ["BTC/USDT"])
    if isinstance(symbol, pd.Series):
        symbol = symbol.iloc[0] if len(symbol) > 0 else "BTC/USDT"

    for i in range(1, len(df)):
        ts = int(df["timestamp"].iloc[i]) if "timestamp" in df.columns else i * 3600000

        if pd.isna(histogram.iloc[i]) or pd.isna(adx.iloc[i]):
            continue
        if pd.isna(histogram.iloc[i - 1]):
            continue

        prev_hist = histogram.iloc[i - 1]
        curr_hist = histogram.iloc[i]
        strong_trend = adx.iloc[i] > adx_threshold

        if position == "flat":
            # MACD crosses above signal + strong trend
            if prev_hist <= 0 and curr_hist > 0 and strong_trend:
                signals.append(_make_signal(
                    "macd_adx_filter", str(symbol), OrderSide.BUY, ts,
                    f"MACD cross up + ADX={adx.iloc[i]:.1f}>{adx_threshold}",
                ))
                position = "long"
            elif prev_hist >= 0 and curr_hist < 0 and strong_trend:
                signals.append(_make_signal(
                    "macd_adx_filter", str(symbol), OrderSide.SELL, ts,
                    f"MACD cross down + ADX={adx.iloc[i]:.1f}>{adx_threshold}",
                ))
                position = "short"

        elif position == "long":
            if prev_hist >= 0 and curr_hist < 0:
                signals.append(_make_signal(
                    "macd_adx_filter", str(symbol), OrderSide.SELL, ts,
                    "Exit long: MACD cross down",
                ))
                position = "flat"

        elif position == "short":
            if prev_hist <= 0 and curr_hist > 0:
                signals.append(_make_signal(
                    "macd_adx_filter", str(symbol), OrderSide.BUY, ts,
                    "Exit short: MACD cross up",
                ))
                position = "flat"

    return signals


def bollinger_rsi_combo(
    df: pd.DataFrame,
    params: dict[str, Any] | None = None,
) -> list[Signal]:
    """Bollinger + RSI combo: price at band with RSI confirmation.

    Entry long: price below lower band AND RSI oversold.
    Entry short: price above upper band AND RSI overbought.
    Exit long: price above upper band OR RSI overbought.
    Exit short: price below lower band OR RSI oversold.
    """
    p = params or {}
    bb_period = p.get("bb_period", 20)
    bb_std = p.get("bb_std", 2.0)
    rsi_period = p.get("rsi_period", 14)
    rsi_oversold = p.get("rsi_oversold", 30)
    rsi_overbought = p.get("rsi_overbought", 70)

    _, bb_upper, bb_lower = _compute_bollinger(df["close"], bb_period, bb_std)
    rsi = _compute_rsi(df["close"], rsi_period)

    signals: list[Signal] = []
    position: str = "flat"
    symbol = df.get("symbol", ["BTC/USDT"])
    if isinstance(symbol, pd.Series):
        symbol = symbol.iloc[0] if len(symbol) > 0 else "BTC/USDT"

    for i in range(1, len(df)):
        ts = int(df["timestamp"].iloc[i]) if "timestamp" in df.columns else i * 3600000

        if pd.isna(rsi.iloc[i]) or pd.isna(bb_lower.iloc[i]) or pd.isna(bb_upper.iloc[i]):
            continue

        price = df["close"].iloc[i]
        at_lower = price < bb_lower.iloc[i]
        at_upper = price > bb_upper.iloc[i]
        is_oversold = rsi.iloc[i] < rsi_oversold
        is_overbought = rsi.iloc[i] > rsi_overbought

        if position == "flat":
            if at_lower and is_oversold:
                signals.append(_make_signal(
                    "bollinger_rsi_combo", str(symbol), OrderSide.BUY, ts,
                    f"Price below lower band + RSI={rsi.iloc[i]:.1f}<{rsi_oversold}",
                ))
                position = "long"
            elif at_upper and is_overbought:
                signals.append(_make_signal(
                    "bollinger_rsi_combo", str(symbol), OrderSide.SELL, ts,
                    f"Price above upper band + RSI={rsi.iloc[i]:.1f}>{rsi_overbought}",
                ))
                position = "short"

        elif position == "long":
            if at_upper or is_overbought:
                signals.append(_make_signal(
                    "bollinger_rsi_combo", str(symbol), OrderSide.SELL, ts,
                    "Exit long: upper band or RSI overbought",
                ))
                position = "flat"

        elif position == "short":
            if at_lower or is_oversold:
                signals.append(_make_signal(
                    "bollinger_rsi_combo", str(symbol), OrderSide.BUY, ts,
                    "Exit short: lower band or RSI oversold",
                ))
                position = "flat"

    return signals


def triple_confirmation(
    df: pd.DataFrame,
    params: dict[str, Any] | None = None,
) -> list[Signal]:
    """Triple confirmation: RSI + MACD + Bollinger all agree.

    Entry long: RSI oversold AND MACD histogram positive AND price below lower band.
    Entry short: RSI overbought AND MACD histogram negative AND price above upper band.
    Exit long: any indicator turns bearish.
    Exit short: any indicator turns bullish.
    """
    p = params or {}
    rsi_period = p.get("rsi_period", 14)
    macd_fast = p.get("macd_fast", 12)
    macd_slow = p.get("macd_slow", 26)
    macd_signal_p = p.get("macd_signal", 9)
    bb_period = p.get("bb_period", 20)
    bb_std = p.get("bb_std", 2.0)

    rsi = _compute_rsi(df["close"], rsi_period)
    _, _, histogram = _compute_macd(df["close"], macd_fast, macd_slow, macd_signal_p)
    _, bb_upper, bb_lower = _compute_bollinger(df["close"], bb_period, bb_std)

    signals: list[Signal] = []
    position: str = "flat"
    symbol = df.get("symbol", ["BTC/USDT"])
    if isinstance(symbol, pd.Series):
        symbol = symbol.iloc[0] if len(symbol) > 0 else "BTC/USDT"

    for i in range(1, len(df)):
        ts = int(df["timestamp"].iloc[i]) if "timestamp" in df.columns else i * 3600000

        if pd.isna(rsi.iloc[i]) or pd.isna(histogram.iloc[i]) or pd.isna(bb_lower.iloc[i]):
            continue

        price = df["close"].iloc[i]
        rsi_oversold = rsi.iloc[i] < 30
        rsi_overbought = rsi.iloc[i] > 70
        macd_bullish = histogram.iloc[i] > 0
        macd_bearish = histogram.iloc[i] < 0
        at_lower = price < bb_lower.iloc[i]
        at_upper = price > bb_upper.iloc[i]

        if position == "flat":
            if rsi_oversold and macd_bullish and at_lower:
                signals.append(_make_signal(
                    "triple_confirmation", str(symbol), OrderSide.BUY, ts,
                    f"Triple bullish: RSI={rsi.iloc[i]:.1f} + MACD histogram>0 + below BB",
                ))
                position = "long"
            elif rsi_overbought and macd_bearish and at_upper:
                signals.append(_make_signal(
                    "triple_confirmation", str(symbol), OrderSide.SELL, ts,
                    f"Triple bearish: RSI={rsi.iloc[i]:.1f} + MACD histogram<0 + above BB",
                ))
                position = "short"

        elif position == "long":
            if rsi_overbought or macd_bearish or at_upper:
                signals.append(_make_signal(
                    "triple_confirmation", str(symbol), OrderSide.SELL, ts,
                    "Exit long: any indicator bearish",
                ))
                position = "flat"

        elif position == "short":
            if rsi_oversold or macd_bullish or at_lower:
                signals.append(_make_signal(
                    "triple_confirmation", str(symbol), OrderSide.BUY, ts,
                    "Exit short: any indicator bullish",
                ))
                position = "flat"

    return signals


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_FUSION_BUILDERS: dict[str, callable] = {
    "rsi_macd_confluence": rsi_macd_confluence,
    "macd_adx_filter": macd_adx_filter,
    "bollinger_rsi_combo": bollinger_rsi_combo,
    "triple_confirmation": triple_confirmation,
}


def build_fusion_strategy_signals(
    strategy_id: str,
    df: pd.DataFrame,
    params: dict[str, Any] | None = None,
) -> list[Signal]:
    """Build signals for a fusion strategy.

    Args:
        strategy_id: Fusion strategy ID (e.g. "rsi_macd_confluence").
        df: DataFrame with OHLCV columns.
        params: Strategy parameters (overrides defaults).

    Returns:
        List of Signal objects.

    Raises:
        ValueError: If strategy_id is not a known fusion strategy.
    """
    builder = _FUSION_BUILDERS.get(strategy_id)
    if builder is None:
        raise ValueError(
            f"Unknown fusion strategy: {strategy_id}. "
            f"Available: {', '.join(_FUSION_BUILDERS.keys())}"
        )
    return builder(df, params)

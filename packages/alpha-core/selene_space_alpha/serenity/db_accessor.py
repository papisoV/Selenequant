"""Serenity DB accessor — sync functions to read from local SQLite databases.

Reads from DerivativesDB (funding rate, OI, taker ratio) and KlineDB
(price, volume) to fill SerenitySnapshot gaps. All functions accept None
for DB instances and return fallback values (graceful degradation).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _to_db_symbol(bare_symbol: str) -> str:
    """Convert bare symbol to DB format: 'BTC' -> 'BTC/USDT'."""
    return f"{bare_symbol}/USDT"


def get_funding_rate_from_db(
    derivatives_db: Any | None,
    symbol: str,
    exchange: str = "binance",
) -> float | None:
    """Get latest funding rate from derivatives.db.

    Returns None if DB unavailable or no data.
    """
    if derivatives_db is None:
        return None
    try:
        db_symbol = _to_db_symbol(symbol)
        df = derivatives_db.get_funding_rates(exchange, db_symbol)
        if df.empty:
            return None
        return float(df.iloc[-1]["funding_rate"])
    except Exception:
        logger.debug("Failed to read funding rate for %s", symbol, exc_info=True)
        return None


def get_oi_change_from_db(
    derivatives_db: Any | None,
    symbol: str,
    exchange: str = "binance",
    interval: str = "1d",
    lookback_days: int = 7,
) -> float | None:
    """Get OI change percentage over lookback period.

    Returns (latest_oi - oi_n_days_ago) / oi_n_days_ago, or None.
    """
    if derivatives_db is None:
        return None
    try:
        db_symbol = _to_db_symbol(symbol)
        df = derivatives_db.get_open_interest(exchange, db_symbol, interval)
        if len(df) < lookback_days + 1:
            return None
        oi_now = float(df.iloc[-1]["open_interest"])
        oi_prev = float(df.iloc[-(lookback_days + 1)]["open_interest"])
        if oi_prev <= 0:
            return None
        return (oi_now - oi_prev) / oi_prev
    except Exception:
        logger.debug("Failed to read OI change for %s", symbol, exc_info=True)
        return None


def get_ls_ratio_from_db(
    derivatives_db: Any | None,
    symbol: str,
    exchange: str = "binance",
    interval: str = "1d",
) -> float | None:
    """Get latest long/short ratio from taker_ratio table.

    Returns buy_sell_ratio from the most recent row, or None.
    """
    if derivatives_db is None:
        return None
    try:
        db_symbol = _to_db_symbol(symbol)
        df = derivatives_db.get_taker_ratio(exchange, db_symbol, interval)
        if df.empty:
            return None
        return float(df.iloc[-1]["buy_sell_ratio"])
    except Exception:
        logger.debug("Failed to read LS ratio for %s", symbol, exc_info=True)
        return None


def get_price_change_7d_from_db(
    kline_db: Any | None,
    symbol: str,
    exchange: str = "binance",
    market_type: str = "futures",
    interval: str = "1d",
) -> float | None:
    """Get 7-day price change from klines.db.

    Returns (close_now - close_7d_ago) / close_7d_ago, or None when the DB or
    data is unavailable. A genuine flat market returns 0.0 — None means "no
    data", never "no change".
    """
    if kline_db is None:
        return None
    try:
        db_symbol = _to_db_symbol(symbol)
        df = kline_db.get_klines(exchange, market_type, db_symbol, interval)
        if len(df) < 8:
            return None
        close_now = float(df.iloc[-1]["close"])
        close_7d = float(df.iloc[-8]["close"])
        if close_7d <= 0:
            return None
        return (close_now - close_7d) / close_7d
    except Exception:
        logger.debug("Failed to read price change for %s", symbol, exc_info=True)
        return None


def get_volume_change_7d_from_db(
    kline_db: Any | None,
    symbol: str,
    exchange: str = "binance",
    market_type: str = "futures",
    interval: str = "1d",
) -> float | None:
    """Get 7-day volume change from klines.db.

    Returns (avg_vol_last_7d - avg_vol_prev_7d) / avg_vol_prev_7d, or None
    when the DB or data is unavailable. A genuine 0% change returns 0.0.
    """
    if kline_db is None:
        return None
    try:
        db_symbol = _to_db_symbol(symbol)
        df = kline_db.get_klines(exchange, market_type, db_symbol, interval)
        if len(df) < 14:
            return None
        vol_last_7d = float(df.iloc[-7:]["volume"].mean())
        vol_prev_7d = float(df.iloc[-14:-7]["volume"].mean())
        if vol_prev_7d <= 0:
            return None
        return (vol_last_7d - vol_prev_7d) / vol_prev_7d
    except Exception:
        logger.debug("Failed to read volume change for %s", symbol, exc_info=True)
        return None

"""Market domain routing and symbol detection for multi-market support.

Provides MarketDomain enum for high-level market classification and
symbol detection functions that route orders to the correct engine.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

MarketType = Literal["spot", "futures", "us_stock", "ashare"]
"""Supported market types — re-exported from selene_space_types.market."""


class MarketDomain(str, Enum):
    """High-level market domain for engine routing.

    Each domain maps to a distinct execution engine (crypto exchange,
    US stock broker, A-share broker).
    """

    CRYPTO = "crypto"
    US_STOCK = "us_stock"
    A_SHARE = "ashare"


# ── Symbol detection ──────────────────────────────────────────────────────────

_CRYPTO_EXCHANGES = frozenset({
    "binance", "okx", "bybit", "bitget", "gate",
    "huobi", "mexc", "kucoin", "coinbase", "kraken",
})

_CRYPTO_QUOTE_CURRENCIES = frozenset({
    "USDT", "BUSD", "USDC", "BTC", "ETH", "FDUSD",
})

_ASHARE_EXCHANGE_SUFFIXES = frozenset({"SZ", "SH", "BJ"})

# Common US stock tickers that might be confused with crypto symbols.
# This is a small denylist; the primary heuristic is alphabetic-only
# without '/' separator and not matching crypto patterns.
_US_STOCK_DENYLIST: frozenset[str] = frozenset()


def is_ashare_symbol(symbol: str) -> bool:
    """Return True if the symbol looks like an A-share ticker.

    A-share patterns: 000001.SZ, 600519.SH, 300750.SZ, 688001.SH, etc.
    """
    if "." not in symbol:
        return False
    parts = symbol.split(".")
    if len(parts) != 2:
        return False
    exchange = parts[1].upper()
    return exchange in _ASHARE_EXCHANGE_SUFFIXES


def is_crypto_symbol(symbol: str, exchange: str = "") -> bool:
    """Return True if the symbol looks like a crypto trading pair.

    Detection strategies:
    1. Known crypto exchange name
    2. Symbol contains '/' separator (e.g. BTC/USDT)
    3. Symbol ends with known quote currency (e.g. BTCUSDT)
    """
    if exchange and exchange.lower() in _CRYPTO_EXCHANGES:
        return True

    sym = symbol.upper().strip()

    if "/" in sym:
        return True

    for quote in _CRYPTO_QUOTE_CURRENCIES:
        if sym.endswith(quote) and len(sym) > len(quote):
            return True

    return False


def is_us_stock_symbol(symbol: str) -> bool:
    """Return True if the symbol looks like a US stock ticker.

    US stock patterns: AAPL, MSFT, TSLA, GOOGL, etc.
    Heuristic: purely alphabetic, 1-5 uppercase letters, no '/' or '.'.
    """
    if not symbol:
        return False

    # A-share symbols have '.' separator — exclude
    if is_ashare_symbol(symbol):
        return False

    # Crypto symbols have '/' or end with quote currencies — exclude
    if is_crypto_symbol(symbol):
        return False

    sym = symbol.strip().upper()

    # Must be purely alphabetic, 1-5 characters (standard US tickers)
    if not sym.isalpha():
        return False

    if len(sym) < 1 or len(sym) > 5:
        return False

    return True


def detect_market_domain(symbol: str, exchange: str = "") -> MarketDomain:
    """Detect the market domain from a symbol string.

    Priority:
    1. A-share pattern (e.g. 600519.SH) → A_SHARE
    2. Crypto pattern (e.g. BTC/USDT) → CRYPTO
    3. US stock pattern (e.g. AAPL) → US_STOCK
    4. Fallback → CRYPTO (existing behavior)
    """
    if is_ashare_symbol(symbol):
        return MarketDomain.A_SHARE

    if is_crypto_symbol(symbol, exchange):
        return MarketDomain.CRYPTO

    if is_us_stock_symbol(symbol):
        return MarketDomain.US_STOCK

    # Default to crypto for backward compatibility
    return MarketDomain.CRYPTO


def market_type_to_domain(market_type: str) -> MarketDomain:
    """Map a MarketType literal to a MarketDomain."""
    if market_type in ("spot", "futures"):
        return MarketDomain.CRYPTO
    if market_type == "us_stock":
        return MarketDomain.US_STOCK
    if market_type == "ashare":
        return MarketDomain.A_SHARE
    return MarketDomain.CRYPTO

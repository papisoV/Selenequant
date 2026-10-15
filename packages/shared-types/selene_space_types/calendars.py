"""Per-market annualization factors and calendar abstraction (Phase 1).

Phase 1 provides per-domain annualization factor tables so that A-share
(~242 trading days/year) and US-stock (~252) backtest metrics stop using
the crypto 365-days/year table. Session-level calendars (holidays, lunch
breaks, intraday session boundaries) are deferred to Phase 2 — only the
``TradingCalendar`` protocol is reserved here.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from selene_space_types.routing import (
    MarketDomain,
    detect_market_domain,
    market_type_to_domain,
)

# ── Crypto: copied verbatim from backtest-engine metrics._bars_table ─────────
# Regression red line: CRYPTO values must stay bit-identical to the legacy
# table (AC-1 golden). Do not "fix" these numbers here.

_CRYPTO_BARS_PER_YEAR: dict[str, int] = {
    "1m": 525_600,
    "3m": 175_200,
    "5m": 105_120,
    "15m": 35_040,
    "30m": 17_520,
    "1h": 8_760,
    "2h": 4_380,
    "4h": 2_190,
    "6h": 1_460,
    "8h": 1_095,
    "12h": 730,
    "1d": 365,
    "1w": 52,
}

_CRYPTO_DAILY_FALLBACK = 365

# ── Equity markets: trading days per year (annual averages) ─────────────────
# ponytail: constants, not year-by-year calendars — Phase 2 session layer
# (exchange-calendars or PROVENANCE'd port) refines if needed.

_A_SHARE_TRADING_DAYS = 242
_US_STOCK_TRADING_DAYS = 252

# Bars per trading day per interval. Approximate — real bar counts depend on
# the data source (e.g. US regular session 6.5h → 1h bars may be 7).
# Only 1d/1w are exact for acceptance purposes (OQ-1).
_EQUITY_SESSION_BARS: dict[str, float] = {
    "1m": 240.0,  # A-share 4h session; US 6.5h ≈ 390 (use min, OQ-1)
    "5m": 48.0,
    "15m": 16.0,
    "30m": 8.0,
    "1h": 4.0,
    "2h": 2.0,
    "4h": 1.0,
}

_EQUITY_DAYS: dict[MarketDomain, int] = {
    MarketDomain.A_SHARE: _A_SHARE_TRADING_DAYS,
    MarketDomain.US_STOCK: _US_STOCK_TRADING_DAYS,
}


def annualization_factor(domain: MarketDomain, interval: str) -> int:
    """Bars per year for a market domain and interval label.

    CRYPTO uses the legacy 365-days/year table verbatim. Equity domains
    derive from trading-days/year × bars/day. Unknown intervals fall back
    to the crypto daily value in the CRYPTO domain (legacy behavior);
    equity domains raise ValueError (no silent fallback, matching the
    optimizer's philosophy).
    """
    key = interval.strip().lower()
    if domain is MarketDomain.CRYPTO:
        return _CRYPTO_BARS_PER_YEAR.get(key, _CRYPTO_DAILY_FALLBACK)
    if key == "1d":
        return _EQUITY_DAYS[domain]
    if key == "1w":
        return 52
    session_bars = _EQUITY_SESSION_BARS.get(key)
    if session_bars is None:
        raise ValueError(
            f"unknown interval {interval!r} for domain {domain.value}; "
            "cannot infer annualization factor"
        )
    return int(session_bars * _EQUITY_DAYS[domain])


def resolve_market_domain(
    kline_market_type: str,
    symbol: str,
    exchange: str = "",
) -> MarketDomain:
    """Resolve the market domain for a kline/bar.

    Only explicit equity market_types (us_stock/ashare) map directly; all
    other values (spot/futures/unknown/empty) fall through to symbol-based
    detection so that misclassified or empty market_type columns are
    corrected by the symbol heuristic rather than silently routed to CRYPTO.
    """
    if kline_market_type == "us_stock":
        return market_type_to_domain(kline_market_type)
    if kline_market_type == "ashare":
        return market_type_to_domain(kline_market_type)
    return detect_market_domain(symbol, exchange)


@runtime_checkable
class TradingCalendar(Protocol):
    """Session-level calendar interface reserved for Phase 2."""

    def annualization_factor(self, interval: str) -> int:
        """Bars per year for the calendar's market domain."""
        ...

    def is_trading_day(self, timestamp: int) -> bool:
        """Whether the (ms epoch) timestamp is a trading day."""
        ...

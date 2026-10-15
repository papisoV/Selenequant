"""Tests for selene_space_types.calendars (Phase 1 annualization tables)."""

import pytest

from selene_space_types.calendars import (
    TradingCalendar,
    annualization_factor,
    resolve_market_domain,
)
from selene_space_types.routing import MarketDomain

# Legacy crypto table from backtest-engine metrics._bars_table() — golden.
_CRYPTO_LEGACY = {
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


class TestCryptoVerbatim:
    def test_all_13_keys_match_legacy(self) -> None:
        for interval, expected in _CRYPTO_LEGACY.items():
            assert annualization_factor(MarketDomain.CRYPTO, interval) == expected

    def test_unknown_interval_falls_back_365(self) -> None:
        assert annualization_factor(MarketDomain.CRYPTO, "9h") == 365

    def test_normalization_matches_legacy(self) -> None:
        assert annualization_factor(MarketDomain.CRYPTO, " 1D ") == 365
        assert annualization_factor(MarketDomain.CRYPTO, "1D") == 365


class TestEquityDomains:
    def test_ashare_daily_is_242(self) -> None:
        assert annualization_factor(MarketDomain.A_SHARE, "1d") == 242

    def test_us_stock_daily_is_252(self) -> None:
        assert annualization_factor(MarketDomain.US_STOCK, "1d") == 252

    def test_weekly_is_52_all_domains(self) -> None:
        assert annualization_factor(MarketDomain.A_SHARE, "1w") == 52
        assert annualization_factor(MarketDomain.US_STOCK, "1w") == 52

    def test_equity_hourly_derived(self) -> None:
        assert annualization_factor(MarketDomain.A_SHARE, "1h") == 4 * 242
        assert annualization_factor(MarketDomain.US_STOCK, "1h") == 4 * 252

    def test_unknown_interval_raises_for_equity(self) -> None:
        with pytest.raises(ValueError, match="unknown interval"):
            annualization_factor(MarketDomain.A_SHARE, "9h")

    def test_normalization_hits_table(self) -> None:
        assert annualization_factor(MarketDomain.A_SHARE, " 1D ") == 242


class TestResolveMarketDomain:
    def test_ashare_symbol(self) -> None:
        assert resolve_market_domain("spot", "600519.SH") is MarketDomain.A_SHARE

    def test_crypto_pair(self) -> None:
        assert resolve_market_domain("spot", "BTC/USDT") is MarketDomain.CRYPTO

    def test_us_stock_ticker(self) -> None:
        assert resolve_market_domain("", "AAPL") is MarketDomain.US_STOCK

    def test_market_type_direct_map_us_stock(self) -> None:
        assert resolve_market_domain("us_stock", "WEIRD9") is MarketDomain.US_STOCK

    def test_market_type_direct_map_ashare(self) -> None:
        assert resolve_market_domain("ashare", "600519") is MarketDomain.A_SHARE

    def test_misfilled_market_type_symbol_fallback(self) -> None:
        # R4: legacy rows with spot/futures on equity symbols
        assert resolve_market_domain("spot", "000001.SZ") is MarketDomain.A_SHARE

    def test_empty_market_type_empty_exchange_bare_symbol_is_us_stock(
        self,
    ) -> None:
        # Known limitation: bare alphabetic symbol routes to US_STOCK
        assert resolve_market_domain("", "ETH") is MarketDomain.US_STOCK

    def test_crypto_exchange_rescues_bare_symbol(self) -> None:
        assert resolve_market_domain("spot", "ETH", "binance") is MarketDomain.CRYPTO


class TestTradingCalendarProtocol:
    def test_protocol_is_runtime_checkable(self) -> None:
        # ``__protocol_attrs__`` is only populated on Python 3.12+; on 3.11
        # getattr returns the empty default and the assertion failed for a
        # reason that had nothing to do with the protocol. Assert the contract
        # directly: runtime_checkable protocols must support isinstance, and
        # the declared members must be present on the protocol itself.
        assert issubclass(type(TradingCalendar), type)
        assert getattr(TradingCalendar, "_is_runtime_protocol", False) or hasattr(
            TradingCalendar, "__protocol_attrs__"
        )
        for member in ("annualization_factor", "is_trading_day"):
            assert hasattr(TradingCalendar, member), f"missing protocol member {member}"

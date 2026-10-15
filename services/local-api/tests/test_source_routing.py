"""Tests for market-domain source routing (app.core.source_routing).

The bug these pin: the runtime favorites sync hardcoded the crypto venues and
fired every added symbol at Binance/OKX, so an A-share code got a live 400 and
the astock sources never received the subscription. Routing must send each
symbol only to the sources that serve its market, at both subscribe and kline
sync time.
"""

from __future__ import annotations

import asyncio


from app.api.v1 import favorites
from app.core.source_routing import (
    intervals_for,
    serves_symbols,
    source_domain,
    sources_for_symbol,
)

# The source pairs a full deployment registers.
_ALL_SOURCES = [
    ("binance", "spot"),
    ("binance", "futures"),
    ("okx", "spot"),
    ("okx", "futures"),
    ("astock", "spot"),
    ("astock", "intraday"),
    ("astock", "market_snapshot"),
    ("yahoo", "us_stock"),
]


class TestSourceDomain:
    def test_astock_exchange_maps_to_ashare(self):
        # market_type is "spot" (reused crypto vocabulary); exchange disambiguates.
        assert source_domain("astock", "spot") == "ashare"
        assert source_domain("astock", "intraday") == "ashare"

    def test_yahoo_maps_to_us_stock(self):
        assert source_domain("yahoo", "us_stock") == "us_stock"

    def test_crypto_venues_map_by_market_type(self):
        assert source_domain("binance", "spot") == "crypto"
        assert source_domain("binance", "futures") == "crypto"
        assert source_domain("okx", "spot") == "crypto"


class TestSourcesForSymbol:
    def test_ashare_symbol_reaches_only_astock(self):
        got = sources_for_symbol("600519.SH", _ALL_SOURCES)
        assert got == [("astock", "spot"), ("astock", "intraday")]

    def test_whole_market_source_is_never_subscribed_per_symbol(self):
        # ("astock","market_snapshot") shares the astock exchange with the
        # per-symbol feeds, so source_domain alone would hand it to every
        # A-share subscribe/watch path. It is whole-market only and must be
        # filtered out — otherwise it would be scoped down to the favorites
        # pool, which is the exact subscription-scoping it exists to escape.
        got = sources_for_symbol("600519.SH", _ALL_SOURCES)
        assert ("astock", "market_snapshot") not in got
        assert all(mt != "market_snapshot" for _, mt in got)
        assert not serves_symbols("astock", "market_snapshot")

    def test_whole_market_source_still_belongs_to_its_domain(self):
        # Filtered from per-symbol routing, but not exiled from the domain —
        # domain identity is what a health/UI consumer reads to place it.
        assert source_domain("astock", "market_snapshot") == "ashare"

    def test_ashare_never_reaches_a_crypto_venue(self):
        got = sources_for_symbol("300750.SZ", _ALL_SOURCES)
        assert all(ex == "astock" for ex, _ in got)
        assert ("binance", "spot") not in got
        assert ("okx", "futures") not in got

    def test_us_ticker_reaches_only_yahoo(self):
        assert sources_for_symbol("AAPL", _ALL_SOURCES) == [("yahoo", "us_stock")]

    def test_us_class_share_routes_to_yahoo(self):
        # detect_market_domain alone falls back to CRYPTO for these; routing
        # must still land them on the equity feed.
        for sym in ("BRK-B", "BRK.A", "BRK$A"):
            assert sources_for_symbol(sym, _ALL_SOURCES) == [("yahoo", "us_stock")], sym

    def test_crypto_pair_reaches_only_crypto_venues(self):
        got = sources_for_symbol("BTCUSDT", _ALL_SOURCES)
        assert got == [
            ("binance", "spot"),
            ("binance", "futures"),
            ("okx", "spot"),
            ("okx", "futures"),
        ]

    def test_unregistered_market_returns_empty(self):
        # A-share symbol but no astock source registered → nothing to send to.
        crypto_only = [("binance", "spot"), ("okx", "futures")]
        assert sources_for_symbol("600519.SH", crypto_only) == []

    def test_whole_market_only_deployment_returns_empty(self):
        # Only the cross-section poller registered: a per-symbol subscribe has
        # nowhere to go, and must not be silently pointed at the poller.
        assert sources_for_symbol("600519.SH", [("astock", "market_snapshot")]) == []


class TestIntervalsFor:
    def test_ashare_pinned_to_daily(self):
        assert intervals_for("astock", "spot", ["30m", "1h"]) == ["1d"]
        assert intervals_for("astock", "intraday", ["30m", "1h"]) == ["1d"]

    def test_crypto_keeps_requested_intervals(self):
        assert intervals_for("binance", "spot", ["30m", "1h"]) == ["30m", "1h"]

    def test_us_keeps_requested_intervals(self):
        assert intervals_for("yahoo", "us_stock", ["30m", "1h"]) == ["30m", "1h"]


class _RecordingService:
    """Minimal MarketDataService stand-in capturing subscribe calls."""

    def __init__(self, sources: list[tuple[str, str]]) -> None:
        self._sources = {pair: object() for pair in sources}
        self.subscribed: list[tuple[str, str]] = []

    @property
    def sources(self) -> dict[tuple[str, str], object]:
        return dict(self._sources)

    async def add_symbol(
        self, symbol: str, *, exchange: str = "binance", market_type: str = "spot"
    ) -> None:
        self.subscribed.append((exchange, market_type))


class _RecordingManager:
    """Captures sync_incremental calls."""

    def __init__(self) -> None:
        self.synced: list[tuple[str, str, str]] = []

    async def sync_incremental(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        days: int = 365,
    ) -> int:
        self.synced.append((exchange, market_type, interval))
        return 0


def _run_sync(symbol: str, sources: list[tuple[str, str]], intervals: list[str]):
    svc = _RecordingService(sources)
    mgr = _RecordingManager()
    asyncio.run(favorites._sync_symbol_klines(symbol, mgr, svc, intervals))
    return svc, mgr


class TestFavoritesSyncRouting:
    """The regression: a runtime-added favorite must route by market domain."""

    def test_ashare_favorite_subscribes_astock_not_crypto(self):
        svc, _ = _run_sync("600519.SH", _ALL_SOURCES, ["30m", "1h"])
        assert set(svc.subscribed) == {("astock", "spot"), ("astock", "intraday")}
        assert all(ex == "astock" for ex, _ in svc.subscribed)

    def test_ashare_favorite_syncs_daily_only(self):
        _, mgr = _run_sync("600519.SH", _ALL_SOURCES, ["30m", "1h"])
        assert mgr.synced  # a sync was attempted
        assert all(ivl == "1d" for _, _, ivl in mgr.synced)

    def test_us_favorite_subscribes_yahoo(self):
        svc, _ = _run_sync("AAPL", _ALL_SOURCES, ["30m", "1h"])
        assert svc.subscribed == [("yahoo", "us_stock")]

    def test_no_source_for_market_subscribes_nothing(self):
        # A-share symbol, crypto-only deployment → no subscribe fires (the old
        # code fired all four crypto venues here).
        svc, mgr = _run_sync("600519.SH", [("binance", "spot")], ["30m", "1h"])
        assert svc.subscribed == []
        assert mgr.synced == []

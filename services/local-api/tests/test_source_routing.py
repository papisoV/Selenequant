"""Tests for market-domain source routing (app.core.source_routing).

The bug these pin: the runtime favorites sync hardcoded the crypto venues and
fired every added symbol at Binance/OKX, so an A-share code got a live 400 and
the astock sources never received the subscription. Routing must send each
symbol only to the sources that serve its market, at both subscribe and kline
sync time.
"""

from __future__ import annotations

import asyncio

import pytest

from app.api.v1 import favorites
from app.core.source_routing import (
    continuous_market,
    intervals_for,
    is_bare_ashare_code,
    market_domain,
    serves_symbols,
    source_domain,
    sources_for_symbol,
    to_ashare_symbol,
    to_favorite_storage,
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


class TestSymbolForms:
    """A-share storage form (bare) vs feed/partition form (suffixed)."""

    def test_bare_code_detected(self):
        assert is_bare_ashare_code("600519")
        assert is_bare_ashare_code(" 920002 ")

    def test_suffixed_or_non_numeric_is_not_bare(self):
        for sym in ("600519.SH", "AAPL", "BTC/USDT", "60051", "6005190", ""):
            assert not is_bare_ashare_code(sym), sym

    def test_bare_code_converts_by_board_rule(self):
        assert to_ashare_symbol("600519") == "600519.SH"
        assert to_ashare_symbol("000001") == "000001.SZ"
        assert to_ashare_symbol("300750") == "300750.SZ"
        assert to_ashare_symbol("688001") == "688001.SH"
        assert to_ashare_symbol("920002") == "920002.BJ"

    def test_conversion_is_idempotent_and_passes_others_through(self):
        assert to_ashare_symbol("600519.SH") == "600519.SH"
        assert to_ashare_symbol(to_ashare_symbol("300750")) == "300750.SZ"
        assert to_ashare_symbol("AAPL") == "AAPL"
        assert to_ashare_symbol("BTC/USDT") == "BTC/USDT"

    def test_storage_form_strips_the_suffix(self):
        assert to_favorite_storage("600519.SH") == "600519"
        assert to_favorite_storage("920002.BJ") == "920002"
        assert to_favorite_storage("600519") == "600519"
        assert to_favorite_storage("AAPL") == "AAPL"
        assert to_favorite_storage("BTC/USDT") == "BTC/USDT"
        # The US class-share separator is not an exchange suffix.
        assert to_favorite_storage("BRK.A") == "BRK.A"
        # Yahoo's Shanghai spelling — the frontend normalizeSymbol strips it.
        assert to_favorite_storage("000001.SS") == "000001"

    def test_bare_code_is_ashare_domain(self):
        # The shared detect_market_domain() says CRYPTO for a bare code — it
        # requires the suffix, and it is consumed by order risk / the engine
        # registry, so the correction lives at the routing boundary instead.
        assert market_domain("600519") == "ashare"

    def test_suffixed_and_non_ashare_domains_unchanged(self):
        assert market_domain("600519.SH") == "ashare"
        assert market_domain("AAPL") == "us_stock"
        assert market_domain("BRK-B") == "us_stock"
        assert market_domain("BTC/USDT") == "crypto"
        assert market_domain("BTCUSDT") == "crypto"


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
    """Minimal MarketDataService stand-in capturing subscribe calls.

    Records the symbol too: the stored favorites form is bare, so the
    regression this guards is "the code we subscribe is suffixed".
    """

    def __init__(self, sources: list[tuple[str, str]]) -> None:
        self._sources = {pair: object() for pair in sources}
        self.subscribed: list[tuple[str, str, str]] = []

    @property
    def sources(self) -> dict[tuple[str, str], object]:
        return dict(self._sources)

    async def add_symbol(
        self, symbol: str, *, exchange: str = "binance", market_type: str = "spot"
    ) -> None:
        self.subscribed.append((exchange, market_type, symbol))


class _RecordingManager:
    """Captures sync_incremental calls (symbol included — it is the partition key)."""

    def __init__(self) -> None:
        self.synced: list[tuple[str, str, str, str]] = []

    async def sync_incremental(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        days: int = 365,
    ) -> int:
        self.synced.append((exchange, market_type, symbol, interval))
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
        assert {(ex, mt) for ex, mt, _ in svc.subscribed} == {
            ("astock", "spot"),
            ("astock", "intraday"),
        }
        assert all(ex == "astock" for ex, _, _ in svc.subscribed)

    def test_ashare_favorite_syncs_daily_only(self):
        _, mgr = _run_sync("600519.SH", _ALL_SOURCES, ["30m", "1h"])
        assert mgr.synced  # a sync was attempted
        assert all(ivl == "1d" for _, _, _, ivl in mgr.synced)

    def test_bare_ashare_favorite_reaches_astock_suffixed(self):
        # The stored form is bare ("600519" — frontend toPair strips the
        # suffix); the feeds and the KlineDB partition key on "600519.SH".
        # Both the subscribe and the sync must carry the suffixed form.
        svc, mgr = _run_sync("600519", _ALL_SOURCES, ["30m", "1h"])
        assert svc.subscribed  # bare code used to resolve to CRYPTO → no target
        assert {sym for _, _, sym in svc.subscribed} == {"600519.SH"}
        assert {sym for _, _, sym, _ in mgr.synced} == {"600519.SH"}

    def test_bare_beijing_favorite_maps_to_bj(self):
        # 92xxxx is Beijing; the board rule (not "SZ by default") must decide.
        svc, _ = _run_sync("920002", _ALL_SOURCES, ["30m", "1h"])
        assert {sym for _, _, sym in svc.subscribed} == {"920002.BJ"}

    def test_bare_and_suffixed_forms_route_identically(self):
        bare, bare_mgr = _run_sync("300750", _ALL_SOURCES, ["30m", "1h"])
        suffixed, suffixed_mgr = _run_sync("300750.SZ", _ALL_SOURCES, ["30m", "1h"])
        assert bare.subscribed == suffixed.subscribed
        assert bare_mgr.synced == suffixed_mgr.synced

    def test_us_favorite_subscribes_yahoo(self):
        svc, _ = _run_sync("AAPL", _ALL_SOURCES, ["30m", "1h"])
        assert svc.subscribed == [("yahoo", "us_stock", "AAPL")]

    def test_no_source_for_market_subscribes_nothing(self):
        # A-share symbol, crypto-only deployment → no subscribe fires (the old
        # code fired all four crypto venues here).
        svc, mgr = _run_sync("600519.SH", [("binance", "spot")], ["30m", "1h"])
        assert svc.subscribed == []
        assert mgr.synced == []

    def test_bare_ashare_with_crypto_only_deployment_subscribes_nothing(self):
        # Same, reached through the bare storage form: `sources_for_symbol`
        # must not fall back to the US-ticker regex ("600519" is 6 chars, so
        # it would not match anyway — but the domain must be A-share, not
        # CRYPTO, or a crypto-only box would happily accept it).
        svc, mgr = _run_sync("600519", [("binance", "spot")], ["30m", "1h"])
        assert svc.subscribed == []
        assert mgr.synced == []


class TestStartupBoundaries:
    """The startup paths consume the same bare storage form as favorites."""

    def test_astock_reachable_set_is_suffixed(self):
        # add_symbol/sync_incremental/the detector all key on the suffix; the
        # stored form is bare. An empty list here is the starvation signal.
        from app.main import _astock_reachable_symbols

        got = _astock_reachable_symbols(
            ["BTC/USDT", "600519", "AAPL", "300750", "920002"]
        )
        assert got == ["600519.SH", "300750.SZ", "920002.BJ"]

    def test_astock_reachable_set_keeps_suffixed_input_idempotent(self):
        from app.main import _astock_reachable_symbols

        assert _astock_reachable_symbols(["600519.SH"]) == ["600519.SH"]

    def test_daily_sync_loop_syncs_the_suffixed_partition_key(self):
        from app.main import _ASTOCK_DAILY_SYNC_INTERVAL, _astock_daily_sync_loop

        class _Mgr:
            def __init__(self):
                self.calls = []

            async def sync_incremental(self, ex, mt, sym, ivl, days=365):
                self.calls.append((ex, mt, sym, ivl))
                raise asyncio.CancelledError  # one pass, then unwind the loop

        mgr = _Mgr()

        async def _run():
            with pytest.raises(asyncio.CancelledError):
                await _astock_daily_sync_loop({"kline_manager": mgr}, ["600519"])

        asyncio.run(_run())
        assert mgr.calls[0] == ("astock", "spot", "600519.SH", "1d")
        assert _ASTOCK_DAILY_SYNC_INTERVAL > 0  # loop constant still patchable

    def test_startup_kline_sync_uses_the_suffixed_partition_key(self):
        # Startup sync receives the same bare storage list as favorites; it
        # must convert before calling sync_incremental (the partition key).
        from app.main import _kline_startup_sync

        class _Mgr:
            def __init__(self):
                self.calls = []

            async def sync_incremental(self, ex, mt, sym, ivl, days=365):
                self.calls.append((ex, mt, sym, ivl))
                return 0

        mgr = _Mgr()
        asyncio.run(
            _kline_startup_sync(mgr, "600519,AAPL", ["30m"], sources=_ALL_SOURCES)
        )
        assert ("astock", "spot", "600519.SH", "1d") in mgr.calls
        assert all(sym != "600519" for _, _, sym, _ in mgr.calls)


class TestContinuousMarket:
    """24/7 markets only — the gap sweeper judges contiguity by wall clock.

    Being *registered* is not the same as being *continuous*. The sweeper's
    domain gate used to accept every registered source, so AAPL's overnight
    and weekend gaps were classified as repairable holes (measured: 391 holes
    / 10,950 missing bars on yahoo/us_stock/1h alone). Repairing them can never
    succeed and burns REST budget every 30 minutes.
    """

    def test_crypto_venues_are_continuous(self):
        assert continuous_market("binance", "spot")
        assert continuous_market("binance", "futures")
        assert continuous_market("okx", "spot")
        assert continuous_market("okx", "futures")

    def test_equity_venues_are_not_continuous(self):
        assert not continuous_market("yahoo", "us_stock")
        assert not continuous_market("astock", "spot")
        assert not continuous_market("astock", "intraday")

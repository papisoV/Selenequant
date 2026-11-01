"""Integration coverage for the market data plumbing (no live venue).

Two concerns, exercised end to end:
1. Source callbacks fan out through MarketDataService onto one shared bus.
2. Binance wire payloads decode into the typed event models.

The source is a scripted double: tests push events in and observe what the
service re-emits to its subscribers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from selene_space_exchange.exchanges.binance.normalizer import BinanceDepthDelta, BinanceNormalizer
from selene_space_exchange.normalizers.symbol import SymbolNormalizer
from selene_space_market.service import MarketDataService
from selene_space_market.source import ExchangeSourceConfig
from selene_space_types.market import KlineEvent, OrderBookL2Snapshot, TickerEvent

BTC = "BTC/USDT"
ETH = "ETH/USDT"
TS_MS = 1_700_000_000_000


@pytest.fixture
def symbol_normalizer() -> SymbolNormalizer:
    return SymbolNormalizer()


@pytest.fixture
def normalizer(symbol_normalizer: SymbolNormalizer) -> BinanceNormalizer:
    return BinanceNormalizer(symbol_normalizer)


def _spot_quote(venue: str = "binance", market: str = "spot") -> TickerEvent:
    return TickerEvent(
        exchange=venue,
        market_type=market,
        symbol=BTC,
        timestamp=TS_MS,
        last_price=42000.0,
        bid=41999.0,
        ask=42001.0,
        volume_24h=1500.0,
        change_24h_pct=1.2,
    )


def _hour_bar(venue: str = "binance", market: str = "spot") -> KlineEvent:
    return KlineEvent(
        exchange=venue,
        market_type=market,
        symbol=BTC,
        timestamp=TS_MS,
        interval="1h",
        open=41500.0,
        high=42500.0,
        low=41000.0,
        close=42000.0,
        volume=100.0,
        closed=True,
    )


class FakeSource:
    """ExchangeSource double built on a per-channel callback registry.

    Instead of one list per channel, channels are keyed by event kind; the
    publish helper walks whichever bucket an injected event belongs to.
    """

    CHANNELS = ("ticker", "kline", "orderbook")

    def __init__(self, exchange: str = "binance", market_type: str = "spot") -> None:
        self._config = ExchangeSourceConfig(exchange=exchange, market_type=market_type)
        self._up = False
        self._watching: set[str] = set()
        self._latest_quote: dict[str, TickerEvent] = {}
        self._latest_bar: dict[str, KlineEvent] = {}
        self._latest_book: dict[str, OrderBookL2Snapshot] = {}
        self._subs: dict[str, list[Callable]] = {ch: [] for ch in self.CHANNELS}

    @property
    def config(self) -> ExchangeSourceConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._up

    async def start(self) -> None:
        self._up = True

    async def stop(self) -> None:
        self._up = False

    async def subscribe(self, symbol: str, **_: Any) -> None:
        self._watching.add(symbol)

    async def unsubscribe(self, symbol: str) -> None:
        self._watching.discard(symbol)

    def get_ticker(self, symbol: str) -> TickerEvent | None:
        return self._latest_quote.get(symbol)

    def get_kline(self, symbol: str) -> KlineEvent | None:
        return self._latest_bar.get(symbol)

    def get_orderbook(self, symbol: str) -> OrderBookL2Snapshot | None:
        return self._latest_book.get(symbol)

    def get_symbols(self) -> list[str]:
        return sorted(self._watching)

    def on_ticker(self, callback: Callable[[TickerEvent], Any]) -> None:
        self._subs["ticker"].append(callback)

    def on_kline(self, callback: Callable[[KlineEvent], Any]) -> None:
        self._subs["kline"].append(callback)

    def on_orderbook(self, callback: Callable[[OrderBookL2Snapshot], Any]) -> None:
        self._subs["orderbook"].append(callback)

    def _emit(self, channel: str, *payload: Any) -> None:
        for cb in self._subs[channel]:
            cb(*payload)

    def inject_ticker(self, symbol: str, event: TickerEvent) -> None:
        self._latest_quote[symbol] = event
        self._emit("ticker", event)

    def inject_kline(self, symbol: str, event: KlineEvent) -> None:
        self._latest_bar[symbol] = event
        self._emit("kline", event)


def _wire_ticker() -> dict[str, Any]:
    return {
        "e": "24hrTicker",
        "s": "BTCUSDT",
        "c": "42000.00",
        "b": "41999.50",
        "a": "42000.50",
        "v": "1500.0",
        "P": "1.2",
        "E": TS_MS,
    }


def _wire_kline() -> dict[str, Any]:
    return {
        "e": "kline",
        "s": "ETHUSDT",
        "E": TS_MS,
        "k": {
            "t": TS_MS,
            "s": "ETHUSDT",
            "i": "1h",
            "o": "2200.00",
            "h": "2250.00",
            "l": "2180.00",
            "c": "2240.00",
            "v": "500.0",
            "x": True,
        },
    }


def _wire_depth() -> dict[str, Any]:
    return {
        "e": "depthUpdate",
        "s": "BTCUSDT",
        "E": TS_MS,
        "U": 100,
        "u": 101,
        "b": [["42000.00", "1.5"]],
        "a": [["42001.00", "0.5"]],
    }


def _service_with(*sources: FakeSource) -> MarketDataService:
    svc = MarketDataService()
    for src in sources:
        svc.register_source(src)
    return svc


class TestBusFanOut:
    """Whatever a registered source emits must surface on the service bus."""

    def test_quote_reaches_bus_subscriber(self) -> None:
        src = FakeSource("binance", "spot")
        svc = _service_with(src)

        seen: list[TickerEvent] = []
        svc.on_ticker(seen.append)
        src.inject_ticker(BTC, _spot_quote())

        assert len(seen) == 1
        assert seen[0].last_price == 42000.0
        assert seen[0].exchange == "binance"

    def test_bar_reaches_bus_subscriber(self) -> None:
        src = FakeSource("okx", "futures")
        svc = _service_with(src)

        seen: list[KlineEvent] = []
        svc.on_kline(seen.append)
        src.inject_kline(BTC, _hour_bar("okx", "futures"))

        assert len(seen) == 1
        assert seen[0].close == 42000.0
        assert seen[0].exchange == "okx"

    def test_distinct_venues_converge_on_one_bus(self) -> None:
        bnc = FakeSource("binance", "spot")
        okx = FakeSource("okx", "futures")
        svc = _service_with(bnc, okx)

        seen: list[TickerEvent] = []
        svc.on_ticker(seen.append)
        bnc.inject_ticker(BTC, _spot_quote("binance", "spot"))
        okx.inject_ticker(BTC, _spot_quote("okx", "futures"))

        assert len(seen) == 2
        assert {t.exchange for t in seen} == {"binance", "okx"}

    @pytest.mark.asyncio
    async def test_symbol_routing_picks_declared_venue(self) -> None:
        bnc = FakeSource("binance", "spot")
        okx = FakeSource("okx", "futures")
        svc = _service_with(bnc, okx)
        await svc.start()

        await svc.add_symbol(ETH, exchange="okx", market_type="futures")

        assert ETH in okx.get_symbols()
        assert ETH not in bnc.get_symbols()
        await svc.stop()


class TestBinanceWireDecoding:
    """Each raw payload kind must decode to the right typed model."""

    def test_ticker_wire_decodes(self, normalizer: BinanceNormalizer) -> None:
        decoded = normalizer.normalize(_wire_ticker())
        assert isinstance(decoded, TickerEvent)
        assert decoded.symbol == BTC
        assert decoded.last_price == 42000.0

    def test_kline_wire_decodes(self, normalizer: BinanceNormalizer) -> None:
        decoded = normalizer.normalize(_wire_kline())
        assert isinstance(decoded, KlineEvent)
        assert decoded.symbol == ETH
        assert decoded.close == 2240.0
        assert decoded.closed is True

    def test_depth_wire_decodes(self, normalizer: BinanceNormalizer) -> None:
        decoded = normalizer.normalize(_wire_depth())
        assert isinstance(decoded, BinanceDepthDelta)
        assert decoded.first_update_id == 100
        assert decoded.last_update_id == 101
        assert len(decoded.delta.bids) == 1
        assert len(decoded.delta.asks) == 1

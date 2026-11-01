"""MarketDataService coverage — source registry lifecycle, symbol routing,
ticker fan-out, the Deribit options single-writer store, and the executor
offload regression for large-order detection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from selene_space_market.service import MarketDataService, MarketDataServiceError
from selene_space_market.source import ExchangeSourceConfig
from selene_space_types.derivatives import (
    LargeOrderEvent,
    OptionsInstrument,
    OptionsSummary,
    OptionsTicker,
)
from selene_space_types.market import (
    KlineEvent,
    OrderBookL2Snapshot,
    TickerEvent,
)

REGISTRY_GRID = [
    ("binance", "spot"),
    ("binance", "futures"),
    ("okx", "spot"),
    ("okx", "futures"),
]

TICKER_SAMPLE = dict(
    timestamp=1700000000000,
    last_price=67000.0,
    bid=66999.0,
    ask=67001.0,
    volume_24h=1000.0,
    change_24h_pct=1.5,
)


class FakeSource:
    """In-memory ExchangeSource double driven by explicit injection.

    Callbacks live in one ``channels`` mapping keyed by feed name; each
    ``inject_*`` replays the stored event through that channel.
    """

    CHANNELS = ("ticker", "kline", "book")

    def __init__(self, exchange: str = "binance", market_type: str = "spot") -> None:
        self._config = ExchangeSourceConfig(exchange=exchange, market_type=market_type)
        self.live = False
        self.watched: set[str] = set()
        self.channels: dict[str, list[Callable]] = {name: [] for name in self.CHANNELS}
        self.latest: dict[str, Any] = {}

    # -- lifecycle -----------------------------------------------------

    @property
    def config(self) -> ExchangeSourceConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self.live

    async def start(self) -> None:
        self.live = True

    async def stop(self) -> None:
        self.live = False

    async def subscribe(self, symbol: str, *, ticker: bool = True, kline: bool = True, depth: bool = True, trade: bool = True, kline_interval: str | None = None) -> None:
        self.watched.add(symbol)

    async def unsubscribe(self, symbol: str) -> None:
        self.watched.discard(symbol)

    # -- reads ---------------------------------------------------------

    def get_ticker(self, symbol: str) -> TickerEvent | None:
        return self.latest.get(f"ticker:{symbol}")

    def get_kline(self, symbol: str) -> KlineEvent | None:
        return self.latest.get(f"kline:{symbol}")

    def get_orderbook(self, symbol: str) -> OrderBookL2Snapshot | None:
        return self.latest.get(f"book:{symbol}")

    def get_symbols(self) -> list[str]:
        return sorted(self.watched)

    # -- wiring + injection ---------------------------------------------

    def on_ticker(self, callback: Callable[[TickerEvent], Any]) -> None:
        self.channels["ticker"].append(callback)

    def on_kline(self, callback: Callable[[KlineEvent], Any]) -> None:
        self.channels["kline"].append(callback)

    def on_orderbook(self, callback: Callable[[OrderBookL2Snapshot], Any]) -> None:
        self.channels["book"].append(callback)

    def _broadcast(self, channel: str, event: Any) -> None:
        for handler in self.channels[channel]:
            handler(event)

    def inject_ticker(self, symbol: str, ticker: TickerEvent) -> None:
        self.latest[f"ticker:{symbol}"] = ticker
        self._broadcast("ticker", ticker)

    def inject_kline(self, symbol: str, kline: KlineEvent) -> None:
        self.latest[f"kline:{symbol}"] = kline
        self._broadcast("kline", kline)


def _sample_ticker(
    exchange: str = "binance", market_type: str = "spot", symbol: str = "BTC/USDT"
) -> TickerEvent:
    return TickerEvent(
        exchange=exchange, market_type=market_type, symbol=symbol, **TICKER_SAMPLE
    )


def _fresh_service(*sources: FakeSource) -> MarketDataService:
    svc = MarketDataService()
    for src in sources:
        svc.register_source(src)
    return svc


class TestLifecycle:
    def test_new_service_is_stopped(self) -> None:
        assert MarketDataService().is_running is False

    @pytest.mark.asyncio
    async def test_start_stop_propagates_to_sources(self) -> None:
        src = FakeSource()
        svc = _fresh_service(src)

        await svc.start()
        assert (svc.is_running, src.is_running) == (True, True)

        await svc.stop()
        assert (svc.is_running, src.is_running) == (False, False)

    @pytest.mark.asyncio
    async def test_repeated_start_is_harmless(self) -> None:
        svc = _fresh_service(FakeSource())
        await svc.start()
        await svc.start()
        assert svc.is_running is True
        await svc.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_silent(self) -> None:
        await MarketDataService().stop()


class TestRegistry:
    def test_miss_returns_none(self) -> None:
        assert MarketDataService().get_source("binance", "spot") is None

    @pytest.mark.parametrize(
        ("exchange", "market_type"), [("okx", "futures"), ("binance", "futures")]
    )
    def test_hit_returns_registered_instance(self, exchange: str, market_type: str) -> None:
        registered = FakeSource(exchange, market_type)
        assert _fresh_service(registered).get_source(exchange, market_type) is registered

    def test_full_exchange_market_grid(self) -> None:
        grid_sources = [FakeSource(*grid) for grid in REGISTRY_GRID]
        assert len(_fresh_service(*grid_sources).sources) == 4


class TestSymbolRouting:
    @pytest.mark.asyncio
    async def test_add_symbol_lands_on_named_source(self) -> None:
        spot = FakeSource("binance", "spot")
        svc = _fresh_service(spot)
        await svc.start()

        await svc.add_symbol("BTC/USDT", exchange="binance", market_type="spot")
        assert "BTC/USDT" in spot.get_symbols()
        await svc.stop()

    @pytest.mark.asyncio
    async def test_unregistered_destination_raises(self) -> None:
        svc = _fresh_service(FakeSource("binance", "spot"))
        with pytest.raises(MarketDataServiceError):
            await svc.add_symbol("BTC/USDT", exchange="okx", market_type="futures")

    def test_symbol_list_starts_empty(self) -> None:
        assert MarketDataService().get_symbols() == []

    def test_event_bus_always_available(self) -> None:
        assert MarketDataService().event_bus is not None


class TestTickerFlow:
    def test_source_injection_surfaces_on_service(self) -> None:
        spot = FakeSource("binance", "spot")
        svc = _fresh_service(spot)

        received: list[TickerEvent] = []
        svc.on_ticker(received.append)

        published = _sample_ticker()
        spot.inject_ticker("BTC/USDT", published)
        assert received == [published]

    def test_reads_are_partition_scoped(self) -> None:
        by_partition = {grid: FakeSource(*grid) for grid in REGISTRY_GRID}
        svc = _fresh_service(*by_partition.values())

        t_binance_spot = _sample_ticker("binance", "spot")
        t_okx_futures = _sample_ticker("okx", "futures")
        by_partition[("binance", "spot")].latest["ticker:BTC/USDT"] = t_binance_spot
        by_partition[("okx", "futures")].latest["ticker:BTC/USDT"] = t_okx_futures

        reads = {
            ("binance", "spot"): svc.get_ticker("BTC/USDT", "binance", "spot"),
            ("okx", "spot"): svc.get_ticker("BTC/USDT", "okx", "spot"),
            ("okx", "futures"): svc.get_ticker("BTC/USDT", "okx", "futures"),
        }
        assert reads[("binance", "spot")] is t_binance_spot
        assert reads[("okx", "spot")] is None
        assert reads[("okx", "futures")] is t_okx_futures


class TestOptionsStoreWriter:
    """Single-writer entry point for the Deribit options stores (ADR-1)."""

    INSTRUMENT_NAME = "BTC-28MAR25-80000-C"
    EXPIRY_MS = 1_742_630_400_000

    def _instrument(self) -> OptionsInstrument:
        return OptionsInstrument(
            instrument_name=self.INSTRUMENT_NAME,
            base_currency="BTC",
            expiration_timestamp=self.EXPIRY_MS,
            strike=80000.0,
            option_type="call",
        )

    def test_all_three_stores_populated(self) -> None:
        svc = MarketDataService()
        ticker = OptionsTicker(
            instrument_name=self.INSTRUMENT_NAME,
            underlying_price=100000.0,
            implied_volatility=0.65,
            timestamp=1_750_000_000_000,
        )
        summary = OptionsSummary(
            base_currency="BTC",
            expiration_timestamp=self.EXPIRY_MS,
            days_to_expiry=5.0,
            timestamp=1_750_000_000_000,
        )
        inst = self._instrument()
        svc.fill_options_data([inst], [ticker], [summary])
        assert svc._options_instrument_store[self.INSTRUMENT_NAME] is inst
        assert svc._options_ticker_store[self.INSTRUMENT_NAME] is ticker
        assert svc._options_summary_store["BTC:1742630400000"] is summary

    def test_next_call_replaces_whole_snapshot(self) -> None:
        svc = MarketDataService()
        svc.fill_options_data([self._instrument()], [], [])
        assert len(svc._options_instrument_store) == 1

        svc.fill_options_data([], [], [])
        emptied = (
            svc._options_instrument_store == {},
            svc._options_ticker_store == {},
            svc._options_summary_store == {},
        )
        assert all(emptied)


@pytest.mark.asyncio
async def test_orderbook_detection_runs_off_loop_and_publishes_back_on_loop():
    """Regression: large-order detection must run off the event loop, and its
    emitted events must publish back on the loop thread — async subscribers
    (e.g. main.py's _on_large_order) require a running loop."""
    import asyncio

    from selene_space_types.market import PriceLevel

    svc = MarketDataService()
    svc.register_source(FakeSource())

    fired: list[LargeOrderEvent] = []

    async def collect(ev: LargeOrderEvent) -> None:
        fired.append(ev)

    svc.on_large_order(collect)

    heavy_bid = OrderBookL2Snapshot(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=1_700_000_000_000,
        sequence_id=1,
        bids=[PriceLevel(price=67000.0, quantity=3.0)],  # $201k > $100k threshold
        asks=[PriceLevel(price=67001.0, quantity=0.1)],
    )

    # Exercise the exact register_source wiring path: the async detection
    # coroutine offloads to a worker thread, then republishes via create_task.
    await svc._on_orderbook_for_detection(heavy_bid)
    await asyncio.sleep(0.05)

    assert len(fired) == 1, "async large-order subscriber should fire via loop-thread publish"
    assert fired[0].symbol == "BTC/USDT"
    assert fired[0].event_type == "appeared"

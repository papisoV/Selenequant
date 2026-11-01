"""MarketDataService — one bus, many exchange sources.

The service owns a registry of ``ExchangeSource`` objects keyed by
``(exchange, market_type)`` and fans every source callback into a single
``EventBus``. On top of routing reads/subscriptions it also hosts the
orderflow analytics stack: large-trade / large-order / liquidation
detectors plus the Kingfisher engines (merged book, footprint, VPVR,
LiqMap, CVD, signals).

CPU-heavy orderbook aggregation is deliberately kept off the event loop:
one single-worker thread executor (serialized like a lock) behind an
async semaphore, with publishes deferred back to loop threads.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Callable, Iterable
from typing import Any

from selene_space_market.aggregators.agg_orderbook import AggregatedOrderbookEngine
from selene_space_market.aggregators.cvd import CVDAggregator
from selene_space_market.aggregators.footprint import FootprintEngine
from selene_space_market.aggregators.heatmap import HeatmapAggregator
from selene_space_market.aggregators.kf_index import KFIndexEngine
from selene_space_market.aggregators.large_order import LargeOrderDetector
from selene_space_market.aggregators.large_trade import LargeTradeDetector
from selene_space_market.aggregators.liquidation import LiquidationAggregator
from selene_space_market.aggregators.liqmap_event import LiqMapEventEngine
from selene_space_market.aggregators.liqmap_kde import LiqMapKDEEngine
from selene_space_market.aggregators.merged_book import MergedBookEngine
from selene_space_market.aggregators.signal_engine import CompositeSignalEngine
from selene_space_market.aggregators.tof import ToxicOrderflowEngine
from selene_space_market.aggregators.vpvr import VPVREngine
from selene_space_market.event_bus import EventBus
from selene_space_market.source import ExchangeSource
from selene_space_types.derivatives import (
    FundingRateEvent,
    LargeTradeEvent,
    LargeOrderEvent,
    OpenInterestSnapshot,
    OptionsTicker,
    OptionsInstrument,
    OptionsSummary,
    MergedBookSnapshot,
    FootprintBarEvent,
    VPVRData,
    LiquidationEvent,
    LiquidationStats,
    LiquidationHeatmapData,
    LiqMapResult,
    CVDBarEvent,
)
from selene_space_types.market import (
    TickerEvent,
    TradeEvent,
    KlineEvent,
    OrderBookL2Snapshot,
    AnomalyEvent,
)

logger = logging.getLogger(__name__)

SourceKey = tuple[str, str]

_PAGE_SIZES: dict[str, int] = {"binance": 1000, "okx": 100}
_DEFAULT_PAGE = 200

#: consecutive empty pages tolerated before declaring the history exhausted
_MAX_EMPTY_PAGES = 2


class MarketDataServiceError(Exception):
    pass


class MarketDataServiceNotRunning(MarketDataServiceError):
    pass


def _bus_listener(
    event_type: type,
) -> Callable[["MarketDataService", Callable[..., Any]], None]:
    """Build an ``on_x(callback)`` method that registers onto the shared bus."""

    def listener(self: "MarketDataService", callback: Callable[..., Any]) -> None:
        self._event_bus.subscribe(event_type, callback)

    listener.__doc__ = f"Register a callback fired for every {event_type.__name__}."
    listener.__name__ = f"listen_{event_type.__name__}"
    return listener


def _dedupe_by_timestamp(events: list[KlineEvent]) -> list[KlineEvent]:
    """Keep the first-seen event per timestamp and sort oldest-first."""
    unique: dict[int, KlineEvent] = {}
    for event in events:
        ts = getattr(event, "timestamp", 0)
        if ts not in unique:
            unique[ts] = event
    return sorted(unique.values(), key=lambda k: k.timestamp)


class MarketDataService:
    def __init__(
        self,
        *,
        large_trade_threshold_usd: float = 50_000.0,
        large_order_threshold_usd: float = 100_000.0,
    ) -> None:
        self._sources: dict[SourceKey, ExchangeSource] = {}
        self._event_bus = EventBus()
        self._running = False

        # orderflow analytics — one instance each, wired below
        self._liquidation_aggregator = LiquidationAggregator()
        self.cvd_aggregator = CVDAggregator()
        self.kf_index_engine = KFIndexEngine()
        self.tof_engine = ToxicOrderflowEngine()
        self.signal_engine = CompositeSignalEngine()
        self.agg_orderbook_engine = AggregatedOrderbookEngine()
        self._large_trade_detector = LargeTradeDetector(
            threshold_usd=large_trade_threshold_usd
        )
        self._large_order_detector = LargeOrderDetector(
            threshold_usd=large_order_threshold_usd
        )

        # Kingfisher terminal engines; KDE results feed the coarse heatmap view
        self.heatmap_aggregator = HeatmapAggregator()
        self.liqmap_engine = LiqMapKDEEngine()
        self.liqmap_engine.on_result(self.heatmap_aggregator.process)
        self._liqmap_event_engine = LiqMapEventEngine()
        self._merged_book_engine = MergedBookEngine()
        self._footprint_engine = FootprintEngine()
        self._vpvr_engine = VPVREngine()

        # derivatives caches; funding/OI are venue-keyed, options use their own ids
        self._open_interest_store: dict[str, OpenInterestSnapshot] = {}
        self._funding_rate_store: dict[str, FundingRateEvent] = {}
        self._options_summary_store: dict[str, OptionsSummary] = {}
        self._options_instrument_store: dict[str, OptionsInstrument] = {}
        self._options_ticker_store: dict[str, OptionsTicker] = {}

        # serialized CPU lane for book work: 1 worker == mutual exclusion,
        # semaphore keeps concurrent async handlers queued without blocking
        # the loop thread.
        self._book_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._book_gate = asyncio.Semaphore(1)

        # FIFO cache (oldest key evicted) for batch kline pulls
        self._kline_cache: dict[str, list[KlineEvent]] = {}
        self._kline_cache_capacity = 20

    # -- source registry ---------------------------------------------------

    def register_source(self, source: ExchangeSource) -> None:
        """Add (or replace) a source; wire its callbacks into our bus.

        Call before :meth:`start`. Replacing an existing key does not stop
        the previous instance.
        """
        key = self._key_of(source)
        self._sources[key] = source

        source.on_ticker(self._event_bus.publish)
        source.on_kline(self._event_bus.publish)
        source.on_orderbook(self._on_orderbook_for_detection)

        # optional hooks exist only on derivatives-capable sources
        wired = (
            ("on_trade", self._on_trade_event),
            ("on_liquidation", self._on_liquidation_event),
            ("on_funding_rate", self._on_funding_rate_event),
            ("on_options_ticker", self._on_options_ticker_event),
        )
        for hook_name, handler in wired:
            if hasattr(source, hook_name):
                getattr(source, hook_name)(handler)

        logger.info("source registered: %s/%s", *key)

    @staticmethod
    def _key_of(source: ExchangeSource) -> SourceKey:
        return (source.config.exchange, source.config.market_type)

    def get_source(
        self, exchange: str = "binance", market_type: str = "spot"
    ) -> ExchangeSource | None:
        return self._sources.get((exchange, market_type))

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def sources(self) -> dict[SourceKey, ExchangeSource]:
        return dict(self._sources)

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        outcomes = await asyncio.gather(
            *(self._start_one(key, src) for key, src in list(self._sources.items()))
        )
        for (key, _), ok in zip(list(self._sources.items()), outcomes, strict=False):
            (logger.info if ok else logger.error)(
                "source %s: %s/%s", "started" if ok else "start failed", *key
            )

        self._running = True
        for engine in (
            self.tof_engine,
            self.agg_orderbook_engine,
            self.signal_engine,
            self.kf_index_engine,
        ):
            engine.set_service(self)
        logger.info("MarketDataService running with %d source(s)", len(self._sources))

    async def _start_one(self, key: SourceKey, source: ExchangeSource) -> bool:
        try:
            await source.start()
            return True
        except Exception as exc:
            logger.warning("start error %s/%s: %s", *key, exc)
            return False

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        await asyncio.gather(
            *(self._stop_one(key, src) for key, src in list(self._sources.items()))
        )
        self._book_executor.shutdown(wait=False, cancel_futures=True)
        logger.info("MarketDataService stopped")

    async def _stop_one(self, key: SourceKey, source: ExchangeSource) -> None:
        try:
            await source.stop()
        except Exception:
            logger.error("source stop failed %s/%s", *key, exc_info=True)

    # -- subscriptions ----------------------------------------------------------

    async def add_symbol(
        self,
        symbol: str,
        *,
        exchange: str = "binance",
        market_type: str = "spot",
        ticker: bool = True,
        kline: bool = True,
        depth: bool = True,
        trade: bool = True,
        kline_interval: str | None = None,
    ) -> None:
        """Forward a subscribe call to the matching source."""
        await self._need_source(exchange, market_type).subscribe(
            symbol,
            ticker=ticker,
            kline=kline,
            depth=depth,
            trade=trade,
            kline_interval=kline_interval,
        )

    async def remove_symbol(
        self,
        symbol: str,
        *,
        exchange: str = "binance",
        market_type: str = "spot",
        kline_interval: str | None = None,
    ) -> None:
        await self._need_source(exchange, market_type).unsubscribe(
            symbol, kline_interval=kline_interval
        )

    # -- routed reads -----------------------------------------------------------

    def _route(self, exchange: str, market_type: str, attr: str) -> Any:
        """Resolve the source holding *attr*, else None."""
        source = self.get_source(exchange, market_type)
        return getattr(source, attr) if source is not None else None

    def get_ticker(
        self, symbol: str, exchange: str = "binance", market_type: str = "spot"
    ) -> TickerEvent | None:
        reader = self._route(exchange, market_type, "get_ticker")
        return reader(symbol) if callable(reader) else None

    def get_kline(
        self, symbol: str, exchange: str = "binance", market_type: str = "spot"
    ) -> KlineEvent | None:
        reader = self._route(exchange, market_type, "get_kline")
        return reader(symbol) if callable(reader) else None

    def get_klines(
        self,
        symbol: str,
        exchange: str = "binance",
        market_type: str = "spot",
        limit: int = 100,
    ) -> list[KlineEvent]:
        reader = self._route(exchange, market_type, "get_klines")
        if callable(reader):
            return list(reader(symbol, limit))
        return []

    def get_orderbook(
        self, symbol: str, exchange: str = "binance", market_type: str = "spot"
    ) -> OrderBookL2Snapshot | None:
        reader = self._route(exchange, market_type, "get_orderbook")
        return reader(symbol) if callable(reader) else None

    def get_symbols(
        self, exchange: str = "binance", market_type: str = "spot"
    ) -> list[str]:
        reader = self._route(exchange, market_type, "get_symbols")
        return list(reader()) if callable(reader) else []

    async def fetch_klines_rest(
        self,
        symbol: str,
        exchange: str = "binance",
        market_type: str = "spot",
        interval: str = "1h",
        limit: int = 200,
        end_time: int | None = None,
        start_time: int | None = None,
    ) -> list[KlineEvent]:
        """Single REST pull via the matching source (no cache)."""
        fetcher = self._route(exchange, market_type, "fetch_klines_rest")
        if callable(fetcher):
            return list(
                await fetcher(
                    symbol, interval, limit, end_time=end_time, start_time=start_time
                )
            )
        return []

    async def fetch_klines_batch(
        self,
        symbol: str,
        exchange: str = "binance",
        market_type: str = "spot",
        interval: str = "1h",
        total: int = 1000,
    ) -> list[KlineEvent]:
        """Collect *total* candles by walking pages backwards from now.

        Pages are prepended as they arrive so the final list runs
        oldest->newest. Stops after repeated empty pages or a suspiciously
        short page (exchange exhausted). Cached per key.
        """
        cache_key = f"{exchange}:{market_type}:{symbol}:{interval}:{total}"
        hit = self._kline_cache.get(cache_key)
        if hit is not None and len(hit) >= total:
            return hit[:total]

        fetch = self._route(exchange, market_type, "fetch_klines_rest")
        if not callable(fetch):
            return []

        page_size = self._page_size_for(exchange)
        collected, pages_used = await self._paginate_backwards(
            fetch, symbol, interval, total, page_size
        )

        result = _dedupe_by_timestamp(collected)[:total]
        self._remember_batch(cache_key, result)
        logger.info(
            "batch klines %s: %d over %d page(s)", cache_key, len(result), pages_used
        )
        return result

    @staticmethod
    def _page_size_for(exchange: str) -> int:
        for vendor, size in _PAGE_SIZES.items():
            if vendor in exchange:
                return size
        return _DEFAULT_PAGE

    async def _paginate_backwards(
        self,
        fetch: Callable[..., Any],
        symbol: str,
        interval: str,
        total: int,
        page_size: int,
    ) -> tuple[list[KlineEvent], int]:
        """Walk REST pages from now backwards; returns (events, pages used).

        Cursor retreats to one tick before the oldest candle of each page.
        Two empty pages in a row end the walk; so does any page less than
        half full (venue has nothing older).
        """
        collected: list[KlineEvent] = []
        cursor: int | None = None
        pages_used = 0
        empty_streak = 0

        while len(collected) < total:
            want = min(page_size, total - len(collected))
            try:
                batch = await fetch(symbol, interval, want, end_time=cursor)
            except Exception:
                logger.warning(
                    "batch page failed %s %s @%s",
                    symbol,
                    interval,
                    cursor,
                    exc_info=True,
                )
                break

            if not batch:
                empty_streak += 1
                if empty_streak >= _MAX_EMPTY_PAGES:
                    break
                continue

            empty_streak = 0
            collected = batch + collected
            pages_used += 1

            oldest = getattr(batch[0], "timestamp", 0)
            if oldest <= 0:
                break
            cursor = oldest - 1

            if len(batch) < max(want // 2, 1):
                break

        return collected, pages_used

    def _remember_batch(self, cache_key: str, result: list[KlineEvent]) -> None:
        if len(self._kline_cache) >= self._kline_cache_capacity:
            self._kline_cache.pop(next(iter(self._kline_cache)))
        self._kline_cache[cache_key] = result

    # -- Kingfisher terminal getters ---------------------------------------------

    def get_merged_book(self, symbol: str) -> MergedBookSnapshot | None:
        return self._merged_book_engine.get_merged(symbol)

    def get_liqmap(
        self, symbol: str, current_price: float = 0.0, window: str = "1h"
    ) -> LiquidationHeatmapData | None:
        return self._liqmap_event_engine.get_heatmap(
            symbol, current_price=current_price, window=window
        )

    def get_footprint(
        self, symbol: str, interval: str = "5m", exchange: str = "binance"
    ) -> FootprintBarEvent | None:
        return self._footprint_engine.get_bar(
            symbol, interval=interval, exchange=exchange
        )

    def get_footprint_history(
        self,
        symbol: str,
        interval: str = "5m",
        exchange: str = "binance",
        limit: int = 50,
    ) -> list[FootprintBarEvent]:
        return self._footprint_engine.get_historical_bars(
            symbol, interval=interval, exchange=exchange, limit=limit
        )

    def get_vpvr(
        self, symbol: str, exchange: str = "binance", window: str = "1h"
    ) -> VPVRData | None:
        return self._vpvr_engine.get_profile(symbol, exchange=exchange, window=window)

    def get_atm_iv(
        self, currency: str = "BTC", target_days: float | None = None
    ) -> float | None:
        """Median ATM IV (calls+puts) for the expiry nearest ``target_days``.

        Returns None when no option summaries are stored or none carry a
        positive IV. ``target_days=None`` picks the nearest expiry outright.
        """
        matches = [
            s
            for s in self._options_summary_store.values()
            if s.base_currency == currency.upper()
        ]
        if not matches:
            return None
        if target_days is not None:
            matches.sort(key=lambda s: abs(s.days_to_expiry - target_days))
        else:
            matches.sort(key=lambda s: s.days_to_expiry)
        summary = matches[0]
        ivs = [iv for iv in (summary.call_iv_atm, summary.put_iv_atm) if iv > 0]
        if not ivs:
            return None
        ivs.sort()
        mid = len(ivs) // 2
        return float((ivs[mid - 1] + ivs[mid]) / 2 if len(ivs) % 2 == 0 else ivs[mid])

    # -- unified subscriptions (bus-registered listeners, generated) -------------

    on_ticker = _bus_listener(TickerEvent)
    on_kline = _bus_listener(KlineEvent)
    on_orderbook = _bus_listener(OrderBookL2Snapshot)
    on_trade = _bus_listener(TradeEvent)
    on_large_trade = _bus_listener(LargeTradeEvent)
    on_large_order = _bus_listener(LargeOrderEvent)
    on_liquidation = _bus_listener(LiquidationEvent)
    on_liquidation_stats = _bus_listener(LiquidationStats)
    on_cvd_bar = _bus_listener(CVDBarEvent)
    on_liqmap_result = _bus_listener(LiqMapResult)
    on_liqmap_heatmap = _bus_listener(LiquidationHeatmapData)
    on_options_ticker = _bus_listener(OptionsTicker)
    on_funding_rate = _bus_listener(FundingRateEvent)
    on_anomaly = _bus_listener(AnomalyEvent)

    def on_footprint_bar(self, callback: Callable[[FootprintBarEvent], Any]) -> None:
        self._footprint_engine.on_bar(callback)

    def on_vpvr_update(self, callback: Callable[[VPVRData], Any]) -> None:
        self._vpvr_engine.on_update(callback)

    # -- internal handlers ---------------------------------------------------------

    def _on_trade_event(self, trade: TradeEvent) -> None:
        """Detect large trades + roll CVD bars, then publish everything."""
        self._event_bus.publish(trade)

        flagged = self._large_trade_detector.process(trade)
        if flagged is not None:
            self._event_bus.publish(flagged)

        notional = trade.price * trade.quantity
        for bar in self.cvd_aggregator.process_trade(
            exchange=trade.exchange,
            symbol=trade.symbol,
            side=trade.side,
            quantity=trade.quantity,
            notional_usd=notional,
            timestamp=trade.timestamp,
        ):
            self._event_bus.publish(bar)

        # NOTE: footprint/VPVR recompute intentionally disabled here — both had
        # zero live consumers while costing O(n) per trade (2026-08-22 incident).
        # Re-enable alongside their chart consumers.

    async def _on_orderbook_for_detection(self, snapshot: OrderBookL2Snapshot) -> None:
        """Large-order scan + merged-book update, off-loop with loop-side publish."""
        async with self._book_gate:
            detected = await asyncio.to_thread(self._book_cpu, snapshot)
        for event in detected:
            self._event_bus.publish(event)

    def _book_cpu(self, snapshot: OrderBookL2Snapshot) -> list[LargeOrderEvent]:
        """Pure state-mutating work; emission stays with the caller/loop."""
        detected = self._large_order_detector.process(snapshot)
        self._merged_book_engine.update(snapshot.exchange, snapshot)
        return detected

    def _on_liquidation_event(self, event: LiquidationEvent) -> None:
        self._event_bus.publish(event)
        self._liquidation_aggregator.process(event)
        self.liqmap_event_engine_refresh(event)

    def liqmap_event_engine_refresh(self, event: LiquidationEvent) -> None:
        """Feed LiqMap and re-broadcast each refreshed stats window."""
        self._liqmap_event_engine.process(event)
        for stats in self._liquidation_aggregator.get_all_stats().values():
            self._event_bus.publish(stats)

    def _on_funding_rate_event(self, event: FundingRateEvent) -> None:
        store_key = f"{event.exchange}:{event.symbol}"
        self._funding_rate_store[store_key] = event
        self.liqmap_engine.update_mark_price(
            event.exchange, event.symbol, event.mark_price
        )
        self._event_bus.publish(event)

    def _on_options_ticker_event(self, event: OptionsTicker) -> None:
        self._options_ticker_store[event.instrument_name] = event
        self._event_bus.publish(event)

    def fill_options_data(
        self,
        instruments: Iterable[OptionsInstrument],
        tickers: Iterable[OptionsTicker],
        summaries: Iterable[OptionsSummary],
    ) -> None:
        """Swap in fresh Deribit option books (sole writer: options poller)."""
        self._options_instrument_store = {
            instrument.instrument_name: instrument for instrument in instruments
        }
        self._options_summary_store = {
            f"{summary.base_currency}:{summary.expiration_timestamp}": summary
            for summary in summaries
        }
        self._options_ticker_store = {
            ticker.instrument_name: ticker for ticker in tickers
        }

    def _need_source(self, exchange: str, market_type: str) -> ExchangeSource:
        source = self.get_source(exchange, market_type)
        if source is None:
            raise MarketDataServiceError(
                f"No source registered for {exchange}/{market_type}"
            )
        return source

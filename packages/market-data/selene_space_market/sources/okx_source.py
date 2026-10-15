"""OKX source adapter (spot + perpetual swap).

Bridges ``OKXWSClient``/``ReconnectManager`` from ``selene_space_exchange``
into the package-wide ``ExchangeSource`` surface. Unified symbols look like
``BTC/USDT``; OKX instrument IDs append ``-SWAP`` for the futures flavor.
Historical candles come over REST: recent bars from ``/market/candles``,
deep pagination through ``/market/history-candles``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable

import httpx

from selene_space_core.resilience import OutboundGateway
from selene_space_exchange import OKXWSClient, ReconnectManager
from selene_space_market.cache import MarketDataCache
from selene_space_market.event_bus import EventBus
from selene_space_market.snapshot_store import SnapshotStore
from selene_space_market.source import ExchangeSourceConfig
from selene_space_types.derivatives import LiquidationEvent
from selene_space_types.market import (
    KlineEvent,
    MarketType,
    OrderBookL2Snapshot,
    TickerEvent,
    TradeEvent,
)

logger = logging.getLogger(__name__)

_CANDLE_URL = "https://www.okx.com/api/v5/market/candles"
_HISTORY_CANDLE_URL = "https://www.okx.com/api/v5/market/history-candles"

_BUFFER_LEN = 200
_DEPTH_PUBLISH_GAP_S = 1.0

# unified interval -> OKX bar string
_BAR: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "4h": "4H",
    "1d": "1D",
    "1w": "1W",
    "1M": "1M",
}


def _bar(interval: str) -> str:
    return _BAR.get(interval, "1H")


def _row_to_kline(row: list, symbol: str, market_type: MarketType, interval: str) -> KlineEvent:
    """OKX candle row: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]."""
    return KlineEvent(
        exchange="okx",
        market_type=market_type,
        symbol=symbol,
        timestamp=int(row[0]),
        interval=interval,
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
        closed=(row[8] == "1") if len(row) > 8 else True,
    )


class OKXSource:
    def __init__(
        self,
        *,
        market_type: MarketType = "spot",
        proxy_url: str | None = None,
        kline_intervals: list[str] | str = "1h",
        kline_interval: str | None = None,
        cache_ttl: float = 60.0,
        cache_max_size: int = 1000,
    ) -> None:
        self._config = ExchangeSourceConfig(exchange="okx", market_type=market_type)
        self._proxy_url = proxy_url

        chosen = kline_interval if kline_interval is not None else kline_intervals
        self._intervals: list[str] = [chosen] if isinstance(chosen, str) else list(chosen) or ["1h"]
        self._primary_interval = self._intervals[0]
        self._suffix = "-SWAP" if market_type == "futures" else ""
        self._running = False

        self._event_bus = EventBus()
        # cache_max_size stays on the public signature for parity, but the
        # per-store LRU capacity is fixed inside MarketDataCache
        self._cache_max_size = cache_max_size
        self._cache = MarketDataCache(ttl_seconds=cache_ttl)
        self._snapshots = SnapshotStore()

        # symbol:interval -> rolling candle window
        self._buffers: dict[str, deque[KlineEvent]] = {}
        self._watched: set[str] = set()
        self._last_depth_publish: dict[str, float] = {}

        self._ws: OKXWSClient | None = None
        self._reconnect: ReconnectManager | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._http = OutboundGateway(
            proxy=self._proxy_url or None,
            default_timeout=httpx.Timeout(15.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            default_circuit=True,
        )

    # -- symbol translation ---------------------------------------------

    def _to_inst_id(self, symbol: str) -> str:
        return symbol.replace("/", "-").upper() + self._suffix

    def _to_symbol(self, inst_id: str) -> str:
        stem = inst_id.upper()
        if stem.endswith("-SWAP"):
            stem = stem[: -len("-SWAP")]
        elif stem.endswith("-FUTURES"):
            stem = stem[: -len("-FUTURES")]
        return stem.replace("-", "/")

    def _relabel(self, event: TickerEvent | KlineEvent | TradeEvent | LiquidationEvent | OrderBookL2Snapshot) -> None:
        """Force our market_type and unify wire symbols to BASE/QUOTE."""
        object.__setattr__(event, "market_type", self._config.market_type)
        if "-" in event.symbol:
            unified = self._to_symbol(event.symbol)
            if unified != event.symbol:
                object.__setattr__(event, "symbol", unified)

    # -- ExchangeSource protocol ------------------------------------------

    @property
    def config(self) -> ExchangeSourceConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return

        want_liq = self._config.market_type == "futures"
        self._ws = OKXWSClient(
            proxy_url=self._proxy_url,
            kline_interval=_bar(self._primary_interval),
            on_ticker=self._handle_ticker,
            on_trade=self._handle_trade,
            on_kline=self._handle_kline,
            on_depth=self._handle_depth,
            on_liquidation=self._handle_liquidation if want_liq else None,
        )
        self._reconnect = ReconnectManager(self._ws)
        name = f"okx-{self._config.market_type}-reconnect"
        self._reconnect_task = asyncio.create_task(self._reconnect.run(), name=name)

        if want_liq:
            asyncio.create_task(self._arm_liquidation_feed())

        self._running = True
        logger.info("OKXSource up (%s)", self._config.market_type)

    async def _arm_liquidation_feed(self) -> None:
        """All-market liquidation orders, futures only; small settle delay."""
        await asyncio.sleep(2.0)
        if self._ws is None:
            return
        try:
            await self._ws.subscribe_liquidation_orders("SWAP")
            logger.info("okx futures liquidation-orders armed")
        except Exception:
            logger.warning("okx liquidation-orders subscribe failed", exc_info=True)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False

        if self._reconnect is not None:
            await self._reconnect.stop()
        task = self._reconnect_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._reconnect_task = None

        await self._http.aclose()
        self._cache.clear()
        self._snapshots.clear()
        self._watched.clear()
        logger.info("OKXSource down (%s)", self._config.market_type)

    async def subscribe(
        self,
        symbol: str,
        *,
        ticker: bool = True,
        kline: bool = True,
        depth: bool = True,
        trade: bool = True,
        kline_interval: str | None = None,
    ) -> None:
        if not self._running or self._ws is None:
            return

        ids = [self._to_inst_id(symbol)]

        if ticker:
            await self._ws.subscribe_ticker(ids)
        if trade:
            await self._ws.subscribe_trades(ids)
        if depth:
            await self._ws.subscribe_depth(ids)
        if kline:
            wanted = [kline_interval] if kline_interval else list(self._intervals)
            for ivl in wanted:
                await self._ws.subscribe_kline(ids, _bar(ivl))
            if kline_interval and kline_interval not in self._intervals:
                self._intervals.append(kline_interval)

        self._watched.add(symbol)
        logger.info("okx/%s subscribed %s (instId=%s)", self._config.market_type, symbol, ids[0])

        if kline:
            for ivl in [kline_interval] if kline_interval else list(self._intervals):
                key = f"{symbol}:{ivl}"
                if key not in self._buffers:
                    asyncio.create_task(self._seed_buffer(symbol, ivl))

    async def unsubscribe(self, symbol: str, kline_interval: str | None = None) -> None:
        if self._ws is None:
            return
        inst_id = self._to_inst_id(symbol)

        if kline_interval:
            stream = f"candle{_bar(kline_interval)}:{inst_id}"
            try:
                await self._ws.unsubscribe([stream])
            except Exception:
                pass
            if kline_interval in self._intervals:
                self._intervals.remove(kline_interval)
            self._buffers.pop(f"{symbol}:{kline_interval}", None)
            return

        streams = [f"tickers:{inst_id}", f"books5:{inst_id}"]
        streams += [f"candle{_bar(ivl)}:{inst_id}" for ivl in self._intervals]
        try:
            await self._ws.unsubscribe(streams)
        except Exception:
            pass
        self._snapshots.remove(symbol)
        self._watched.discard(symbol)

    # -- synchronous readers ------------------------------------------------

    def get_ticker(self, symbol: str) -> TickerEvent | None:
        return self._cache.get_ticker(symbol)

    def get_kline(self, symbol: str) -> KlineEvent | None:
        return self._cache.get_kline(symbol)

    def get_klines(self, symbol: str, limit: int = 100) -> list[KlineEvent]:
        primary = self._buffers.get(f"{symbol}:{self._primary_interval}")
        if primary:
            return list(primary)[-limit:]
        pooled: list[KlineEvent] = []
        for ivl in self._intervals:
            pooled.extend(self._buffers.get(f"{symbol}:{ivl}") or ())
        pooled.sort(key=lambda k: k.timestamp)
        return pooled[-limit:]

    def get_orderbook(self, symbol: str) -> OrderBookL2Snapshot | None:
        return self._snapshots.get_snapshot(symbol)

    def get_symbols(self) -> list[str]:
        return sorted(self._watched)

    # -- callback registration ----------------------------------------------

    def on_ticker(self, callback: Callable[[TickerEvent], object]) -> None:
        self._event_bus.subscribe(TickerEvent, callback)

    def on_kline(self, callback: Callable[[KlineEvent], object]) -> None:
        self._event_bus.subscribe(KlineEvent, callback)

    def on_orderbook(self, callback: Callable[[OrderBookL2Snapshot], object]) -> None:
        self._event_bus.subscribe(OrderBookL2Snapshot, callback)

    def on_trade(self, callback: Callable[[TradeEvent], object]) -> None:
        self._event_bus.subscribe(TradeEvent, callback)

    def on_liquidation(self, callback: Callable[[LiquidationEvent], object]) -> None:
        self._event_bus.subscribe(LiquidationEvent, callback)

    # -- REST history ---------------------------------------------------------

    async def fetch_klines_rest(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 200,
        end_time: int | None = None,
        start_time: int | None = None,
    ) -> list[KlineEvent]:
        """Pull candles over REST.

        ``end_time`` paginates *backwards* (bars strictly before it). Deep
        pages go to ``history-candles`` (100/req, years of coverage); the
        regular endpoint holds only ~1440 recent bars but allows 300/req.
        ``start_time`` is accepted for interface parity with binance_source;
        OKX has no forward-from param here, so it narrows the result.
        """
        inst_id = self._to_inst_id(symbol)
        bar = _bar(interval)
        rows: list = []

        try:
            if end_time is not None:
                hist = await self._http.get(
                    _HISTORY_CANDLE_URL,
                    params={
                        "instId": inst_id,
                        "bar": bar,
                        "limit": str(min(limit, 100)),
                        "after": str(end_time),
                    },
                )
                hist.raise_for_status()
                body = hist.json()
                if body.get("code") == "0":
                    rows = body.get("data", [])

            if not rows:
                params: dict[str, str] = {"instId": inst_id, "bar": bar, "limit": str(min(limit, 300))}
                if end_time is not None:
                    params["after"] = str(end_time)
                resp = await self._http.get(_CANDLE_URL, params=params)
                resp.raise_for_status()
                body = resp.json()
                if body.get("code") == "0":
                    rows = body.get("data", [])

            # wire rows are newest-first
            events = [_row_to_kline(r, symbol, self._config.market_type, interval) for r in reversed(rows)]
            if start_time is not None:
                events = [e for e in events if e.timestamp >= start_time]
            return events
        except Exception:
            logger.warning(
                "okx REST klines failed %s %s (%s)",
                symbol,
                interval,
                self._config.market_type,
                exc_info=True,
            )
            return []

    async def _seed_buffer(self, symbol: str, ivl: str) -> None:
        """Prime a symbol's rolling buffer with one REST page."""
        inst_id = self._to_inst_id(symbol)
        try:
            resp = await self._http.get(
                _CANDLE_URL,
                params={"instId": inst_id, "bar": _bar(ivl), "limit": str(_BUFFER_LEN)},
            )
            resp.raise_for_status()
            rows = resp.json().get("data", [])
        except Exception:
            logger.warning("okx buffer seed failed %s %s", symbol, ivl, exc_info=True)
            return

        buf: deque[KlineEvent] = deque(maxlen=_BUFFER_LEN)
        buf.extend(_row_to_kline(r, symbol, self._config.market_type, ivl) for r in reversed(rows))
        self._buffers[f"{symbol}:{ivl}"] = buf
        logger.info("okx seeded %d %s candles for %s", len(buf), ivl, symbol)

    # -- wire handlers ---------------------------------------------------------

    def _handle_ticker(self, event: TickerEvent) -> None:
        self._relabel(event)
        self._cache.put_ticker(event.symbol, event)
        self._event_bus.publish(event)

    def _handle_trade(self, event: TradeEvent) -> None:
        self._relabel(event)
        self._event_bus.publish(event)

    def _handle_liquidation(self, event: LiquidationEvent) -> None:
        self._relabel(event)
        self._event_bus.publish(event)

    def _handle_kline(self, event: KlineEvent) -> None:
        self._relabel(event)
        self._cache.put_kline(event.symbol, event)

        key = f"{event.symbol}:{event.interval}" if event.interval else event.symbol
        buf = self._buffers.get(key)
        if buf is not None:
            if buf and buf[-1].timestamp == event.timestamp:
                buf[-1] = event
            else:
                buf.append(event)
        self._event_bus.publish(event)

    def _handle_depth(self, snapshot: OrderBookL2Snapshot) -> None:
        self._relabel(snapshot)
        self._snapshots.update(snapshot.symbol, snapshot)

        now = time.monotonic()
        if now - self._last_depth_publish.get(snapshot.symbol, 0.0) < _DEPTH_PUBLISH_GAP_S:
            return
        self._last_depth_publish[snapshot.symbol] = now
        self._event_bus.publish(snapshot)

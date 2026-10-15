"""Binance source adapter (spot + USDT-M futures).

Bridges ``BinanceWSClient`` / ``BinanceL2Manager`` / ``ReconnectManager``
from ``selene_space_exchange`` into the package-wide ``ExchangeSource``
surface.

Futures-specific behavior baked in here:

* all-market ``!forceOrder`` and ``!markPrice`` streams on start;
* REST pollers for per-symbol 24h ticker and funding — the futures
  ``@ticker``/``@markPrice`` WS streams are silent through local proxies,
  so polls replace them, with bounded concurrency, hard per-request
  timeout, and consecutive-failure backoff;
* depth snapshots come from an L2 diff manager; after any reconnect the
  books get one bounded re-init round.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable

import httpx

from selene_space_core.resilience import OutboundGateway
from selene_space_exchange import (
    BinanceL2Manager,
    BinanceNormalizer,
    BinanceWSClient,
    ReconnectManager,
    SymbolNormalizer,
)
from selene_space_exchange.exchanges.binance.normalizer import BinanceDepthDelta
from selene_space_market.cache import MarketDataCache, TTLLRUCache
from selene_space_market.event_bus import EventBus
from selene_space_market.snapshot_store import SnapshotStore
from selene_space_market.source import ExchangeSourceConfig
from selene_space_types.derivatives import FundingRateEvent, LiquidationEvent
from selene_space_types.market import (
    KlineEvent,
    MarketType,
    OrderBookL2Snapshot,
    TickerEvent,
    TradeEvent,
)

logger = logging.getLogger(__name__)

_SPOT_WS = "wss://data-stream.binance.vision/ws"
_SPOT_DEPTH_REST = "https://data-api.binance.vision/api/v3/depth"
# spot has an official market-data-only mirror; futures does not (verified
# 2026-09-25: docs list only fapi.binance.com, fapi.binance.vision dead) —
# futures relies on retry/backoff instead
_SPOT_KLINES_URLS = (
    "https://api.binance.com/api/v3/klines",
    "https://data-api.binance.vision/api/v3/klines",
)
FUTURES_WS = "wss://fstream.binance.com/ws"
FUTURES_DEPTH_REST = "https://fapi.binance.com/fapi/v1/depth"
FUTURES_KLINES_URLS = ("https://fapi.binance.com/fapi/v1/klines",)

# futures-only REST poll targets
TICKER_24H_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
PREMIUM_INDEX_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"

_BUFFER_LEN = 200
_DEPTH_PUBLISH_GAP_S = 1.0

# poller tuning: proxy hangs must cost one request, not the backend
_POLL_TICKER_EVERY_S = 10.0
_POLL_FUNDING_EVERY_S = 30.0
_POLL_TIMEOUT_S = 8.0
_POLL_PARALLELISM = 5
_POLL_BACKOFF_CAP_S = 60.0
_L2_REINIT_BUDGET_S = 8.0

# binance interval -> open-time duration in ms (for closed-candle detection)
INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}

# REST klines retry/backoff (0925 QD crypto.py:905-950 同构): one retry after
# each failed URL attempt, half-second then 1.5s backoff. Only retryable on
# 429/418/5xx/network — other 4xx are deterministic (bad symbol/interval).
_KLINES_BACKOFF_S = (0.5, 1.5)

# short-TTL page cache for "latest window" klines REST (no start/end_time).
# Keyed (url_index-independent, symbol, interval, limit); TTL per interval so
# a 1m chart never reads a 2-minute-old page while a 1w chart may. Pagination
# (end_time/start_time) bypasses — keys would explode and hit-rate ~0.
_KLINES_CACHE_TTL_S: dict[str, float] = {
    "1m": 10.0,
    "5m": 20.0,
    "15m": 30.0,
    "30m": 30.0,
    "1h": 60.0,
    "2h": 60.0,
    "4h": 60.0,
    "6h": 60.0,
    "8h": 60.0,
    "12h": 60.0,
    "1d": 120.0,
    "3d": 120.0,
    "1w": 120.0,
}


def _wire(symbol: str) -> str:
    """Unified BASE/QUOTE -> exchange wire symbol."""
    return symbol.replace("/", "").lower()


class BinanceSource:
    def __init__(
        self,
        *,
        market_type: MarketType = "spot",
        ws_url: str | None = None,
        rest_url: str | None = None,
        proxy_url: str | None = None,
        kline_intervals: list[str] | str = "1h",
        kline_interval: str | None = None,
        cache_ttl: float = 60.0,
        cache_max_size: int = 1000,
    ) -> None:
        self._config = ExchangeSourceConfig(exchange="binance", market_type=market_type)
        self._is_futures = market_type == "futures"
        self._ws_url = ws_url or (FUTURES_WS if self._is_futures else _SPOT_WS)
        self._depth_rest_url = rest_url or (
            FUTURES_DEPTH_REST if self._is_futures else _SPOT_DEPTH_REST
        )
        self._klines_rest_urls = (
            FUTURES_KLINES_URLS if self._is_futures else _SPOT_KLINES_URLS
        )
        self._proxy_url = proxy_url

        chosen = kline_interval if kline_interval is not None else kline_intervals
        self._intervals: list[str] = (
            [chosen] if isinstance(chosen, str) else list(chosen) or ["1h"]
        )
        self._primary_interval = self._intervals[0]
        self._running = False

        self._event_bus = EventBus()
        # cache_max_size stays on the public signature for parity, but the
        # per-store LRU capacity is fixed inside MarketDataCache
        self._cache_max_size = cache_max_size
        self._cache = MarketDataCache(ttl_seconds=cache_ttl)
        self._snapshots = SnapshotStore()
        self._normalizer_names = SymbolNormalizer()
        self._normalizer = BinanceNormalizer(self._normalizer_names)

        # symbol:interval -> rolling candle window
        self._buffers: dict[str, deque[KlineEvent]] = {}
        self._watched: set[str] = set()
        self._last_depth_publish: dict[str, float] = {}

        self._ws: BinanceWSClient | None = None
        self._l2: BinanceL2Manager | None = None
        self._reconnect: ReconnectManager | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._ticker_poll_task: asyncio.Task[None] | None = None
        self._funding_poll_task: asyncio.Task[None] | None = None
        self._poll_fail_streak = 0

        # main data client vs dedicated poller client: a hung proxy holding the
        # shared pool once starved the whole backend (PoolTimeout cascade).
        self._http = OutboundGateway(
            proxy=self._proxy_url or None,
            default_timeout=httpx.Timeout(15.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self._poll_http = OutboundGateway(
            proxy=self._proxy_url or None,
            default_timeout=httpx.Timeout(_POLL_TIMEOUT_S, connect=10.0),
            limits=httpx.Limits(max_connections=_POLL_PARALLELISM),
        )
        self._poll_slots = asyncio.Semaphore(_POLL_PARALLELISM)
        # latest-window klines page cache (module TTLLRUCache, thread-safe);
        # per-entry TTL comes from _KLINES_CACHE_TTL_S at put() time
        self._klines_pages = TTLLRUCache[list[dict]](ttl_seconds=120.0, max_size=2048)

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

        self._l2 = BinanceL2Manager(
            http_client=self._http,
            symbol_normalizer=self._normalizer_names,
            rest_url=self._depth_rest_url,
        )
        want_derivatives = self._is_futures
        self._ws = BinanceWSClient(
            url=self._ws_url,
            proxy_url=self._proxy_url,
            symbol_normalizer=self._normalizer_names,
            on_ticker=self._handle_ticker,
            on_trade=self._handle_trade,
            on_kline=self._handle_kline,
            on_depth_update=self._handle_depth_delta,
            on_liquidation=self._handle_liquidation if want_derivatives else None,
            on_funding_rate=self._handle_funding if want_derivatives else None,
            on_connect=self._after_ws_connect,
        )
        self._reconnect = ReconnectManager(self._ws)
        tag = f"binance-{self._config.market_type}"
        self._reconnect_task = asyncio.create_task(
            self._reconnect.run(), name=f"{tag}-reconnect"
        )

        if want_derivatives:
            asyncio.create_task(self._arm_global_streams())
            self._ticker_poll_task = asyncio.create_task(
                self._poll_loop(
                    "ticker", _POLL_TICKER_EVERY_S, 2.0, self._poll_ticker_one
                ),
                name=f"{tag}-rest-ticker",
            )
            self._funding_poll_task = asyncio.create_task(
                self._poll_loop(
                    "funding", _POLL_FUNDING_EVERY_S, 3.0, self._poll_funding_one
                ),
                name=f"{tag}-rest-funding",
            )

        self._running = True
        logger.info(
            "BinanceSource up (%s, ws=%s)", self._config.market_type, self._ws_url
        )

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

        for bg in (self._ticker_poll_task, self._funding_poll_task):
            if bg is not None and not bg.done():
                bg.cancel()
                try:
                    await bg
                except asyncio.CancelledError:
                    pass
        self._ticker_poll_task = None
        self._funding_poll_task = None

        if self._l2 is not None:
            await self._l2.close()

        await self._http.aclose()
        await self._poll_http.aclose()
        self._cache.clear()
        self._snapshots.clear()
        self._watched.clear()
        logger.info("BinanceSource down (%s)", self._config.market_type)

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

        wire_syms = [_wire(symbol)]

        if ticker and not self._is_futures:
            # spot pushes @ticker fine; futures ticker arrives via REST polling
            await self._ws.subscribe_ticker(wire_syms)
        if trade:
            await self._ws.subscribe_agg_trade(wire_syms)
        if kline:
            wanted = [kline_interval] if kline_interval else list(self._intervals)
            for ivl in wanted:
                await self._ws.subscribe_kline(wire_syms, ivl)
            if kline_interval and kline_interval not in self._intervals:
                self._intervals.append(kline_interval)
        if depth:
            upper = _wire(symbol).upper()
            try:
                await self._l2.initialize(upper)  # type: ignore[union-attr]
            except Exception:
                logger.warning("L2 init failed %s", symbol, exc_info=True)
            await self._ws.subscribe_depth(wire_syms)

        self._watched.add(symbol)
        logger.info("binance/%s subscribed %s", self._config.market_type, symbol)

        if kline:
            for ivl in [kline_interval] if kline_interval else list(self._intervals):
                key = f"{symbol}:{ivl}"
                if key not in self._buffers:
                    asyncio.create_task(self._seed_buffer(symbol, ivl))

    async def unsubscribe(self, symbol: str, kline_interval: str | None = None) -> None:
        if self._ws is None:
            return
        wire_sym = _wire(symbol)

        if kline_interval:
            stream = f"{wire_sym}@kline_{kline_interval}"
            try:
                await self._ws.unsubscribe([stream])
            except Exception:
                pass
            if kline_interval in self._intervals:
                self._intervals.remove(kline_interval)
            self._buffers.pop(f"{symbol}:{kline_interval}", None)
            return

        streams = [f"{wire_sym}@ticker", f"{wire_sym}@depth@100ms"]
        streams += [f"{wire_sym}@kline_{ivl}" for ivl in self._intervals]
        for stream in streams:
            try:
                await self._ws.unsubscribe([stream])
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

    def on_funding_rate(self, callback: Callable[[FundingRateEvent], object]) -> None:
        self._event_bus.subscribe(FundingRateEvent, callback)

    # -- futures global streams ------------------------------------------------

    async def _arm_global_streams(self) -> None:
        """All-market forceOrder + markPrice feeds (futures); settle delay first."""
        await asyncio.sleep(2.0)
        if self._ws is None:
            return
        for label, opener in (
            ("!forceOrder@arr", self._ws.subscribe_force_order),
            ("!markPrice@arr@1s", self._ws.subscribe_mark_price),
        ):
            try:
                await opener()
                logger.info("binance futures armed %s", label)
            except Exception:
                logger.warning("subscribe failed for %s", label, exc_info=True)

    # -- futures REST pollers ----------------------------------------------------

    async def _poll_loop(
        self,
        kind: str,
        base_every_s: float,
        initial_delay_s: float,
        poll_one: Callable[[str], Awaitable[None]],
    ) -> None:
        await asyncio.sleep(initial_delay_s)
        while self._running:
            await self._run_round(poll_one)
            await asyncio.sleep(self._next_wait(base_every_s))

    async def _run_round(self, poll_one: Callable[[str], Awaitable[None]]) -> None:
        symbols = list(self._watched)
        if not symbols:
            return

        async def guarded(sym: str) -> bool:
            try:
                async with self._poll_slots:
                    async with asyncio.timeout(_POLL_TIMEOUT_S):
                        await poll_one(sym)
                return True
            except asyncio.TimeoutError:
                logger.warning("%s poll timeout (>=%.0fs)", sym, _POLL_TIMEOUT_S)
                return False
            except Exception:
                logger.warning("%s poll failed", sym, exc_info=True)
                return False

        outcomes = await asyncio.gather(*(guarded(s) for s in symbols))
        if any(outcomes):
            self._poll_fail_streak = 0
        else:
            self._poll_fail_streak += 1
            logger.warning(
                "poll round lost (%d/%d failed, streak=%d)",
                len(outcomes) - sum(outcomes),
                len(outcomes),
                self._poll_fail_streak,
            )

    def _next_wait(self, base_s: float) -> float:
        """Exponential backoff on total-round failures; reset happens via streak=0."""
        if self._poll_fail_streak <= 1:
            return base_s
        return float(
            min(base_s * (2 ** (self._poll_fail_streak - 1)), _POLL_BACKOFF_CAP_S)
        )

    def _drop_dead_symbol(self, symbol: str, kind: str, status: int) -> None:
        # ponytail: 400/404 = permanently invalid on this venue (favorites may
        # hold bare "PEPE" or stock tickers like "AAPL"); without removal the
        # REST loop spams warnings forever. 429/5xx stay retried.
        if status in (400, 404) and symbol in self._watched:
            self._watched.discard(symbol)
            logger.warning(
                "REST %s %s HTTP %s — dropped from poll set (invalid symbol)",
                kind,
                symbol,
                status,
            )
            return
        logger.warning("REST %s %s HTTP %s", kind, symbol, status)

    async def _poll_ticker_one(self, symbol: str) -> None:
        wire_up = _wire(symbol).upper()
        resp = await self._poll_http.get(TICKER_24H_URL, params={"symbol": wire_up})
        if resp.status_code >= 400:
            self._drop_dead_symbol(symbol, "ticker", resp.status_code)
            return
        d = resp.json()
        unified = self._normalizer_names.to_unified("binance", wire_up)
        self._handle_ticker(
            TickerEvent(
                exchange="binance",
                symbol=unified,
                timestamp=int(d.get("closeTime", time.time() * 1000)),
                last_price=float(d["lastPrice"]),
                bid=float(d.get("bidPrice", 0) or 0),
                ask=float(d.get("askPrice", 0) or 0),
                volume_24h=float(d["volume"]),
                quote_volume_24h=float(d.get("quoteVolume", 0) or 0),
                high_24h=float(d.get("highPrice", 0) or 0),
                low_24h=float(d.get("lowPrice", 0) or 0),
                change_24h_pct=float(d["priceChangePercent"]),
            )
        )

    async def _poll_funding_one(self, symbol: str) -> None:
        wire_up = _wire(symbol).upper()
        resp = await self._poll_http.get(PREMIUM_INDEX_URL, params={"symbol": wire_up})
        if resp.status_code >= 400:
            self._drop_dead_symbol(symbol, "funding", resp.status_code)
            return
        d = resp.json()
        unified = self._normalizer_names.to_unified("binance", wire_up)
        self._handle_funding(
            FundingRateEvent(
                exchange="binance",
                symbol=unified,
                funding_rate=float(d.get("lastFundingRate", 0) or 0),
                mark_price=float(d["markPrice"]),
                index_price=float(d.get("indexPrice", 0) or 0),
                next_funding_time=int(d.get("nextFundingTime", 0) or 0),
                timestamp=int(d.get("time", time.time() * 1000)),
            )
        )

    # -- REST history -------------------------------------------------------------

    async def _klines_get(self, params: dict[str, str | int]) -> list[dict]:
        """Fetch one klines page with mirror rotation (spot) + retry/backoff.

        URLs are tried in order; each gets two retries after backoff (0.5s,
        1.5s) before moving to the next. 429/418/5xx/network errors are
        retryable; other 4xx fail fast (deterministic bad request).
        """
        last_exc: Exception | None = None
        for url in self._klines_rest_urls:
            for attempt in range(len(_KLINES_BACKOFF_S) + 1):
                try:
                    resp = await self._http.get(url, params=params)
                    if resp.status_code < 400:
                        rows: list[dict] = resp.json()
                        return rows
                    if resp.status_code not in (418, 429) and resp.status_code < 500:
                        resp.raise_for_status()  # deterministic 4xx: no retry
                    # retryable status (429/418/5xx): record, backoff, maybe hop
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {resp.status_code} for {url}",
                        request=resp.request,
                        response=resp,
                    )
                except httpx.HTTPStatusError:
                    raise  # deterministic 4xx from raise_for_status — fatal
                except Exception as exc:
                    last_exc = exc  # network error — retryable
                if attempt < len(_KLINES_BACKOFF_S):
                    await asyncio.sleep(_KLINES_BACKOFF_S[attempt])
        assert last_exc is not None
        raise last_exc

    async def fetch_klines_rest(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 200,
        end_time: int | None = None,
        start_time: int | None = None,
    ) -> list[KlineEvent]:
        wire_up = _wire(symbol).upper()
        params: dict[str, str | int] = {
            "symbol": wire_up,
            "interval": interval,
            "limit": limit,
        }
        if end_time is not None:
            params["endTime"] = end_time
        if start_time is not None:
            params["startTime"] = start_time

        # latest-window page cache: identical repeat polls within the per-interval
        # TTL never leave the box (pagination bypasses — see _KLINES_CACHE_TTL_S)
        page_key = ""
        ttl = _KLINES_CACHE_TTL_S.get(interval)
        if end_time is None and start_time is None and ttl is not None:
            page_key = f"{wire_up}|{interval}|{limit}"
            cached = self._klines_pages.get(page_key)
            if cached is not None:
                rows = cached
            else:
                rows = await self._klines_get(params)
                self._klines_pages.put(page_key, rows, ttl_seconds=ttl)
        else:
            rows = await self._klines_get(params)

        now_ms = int(time.time() * 1000)
        step_ms = INTERVAL_MS.get(interval, 3_600_000)
        out: list[KlineEvent] = []
        for row in rows:
            open_ts = int(row[0])
            out.append(
                KlineEvent(
                    exchange="binance",
                    market_type=self._config.market_type,
                    symbol=symbol,
                    timestamp=open_ts,
                    interval=interval,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    closed=now_ms >= open_ts + step_ms,
                )
            )
        logger.info("REST klines %s %s: %d", symbol, interval, len(out))
        return out

    async def _seed_buffer(self, symbol: str, ivl: str) -> None:
        """Prime a rolling buffer from one REST page of recent candles."""
        try:
            rows = await self._klines_get(
                {"symbol": _wire(symbol).upper(), "interval": ivl, "limit": _BUFFER_LEN}
            )
        except Exception:
            logger.warning("buffer seed failed %s %s", symbol, ivl, exc_info=True)
            return

        buf: deque[KlineEvent] = deque(maxlen=_BUFFER_LEN)
        buf.extend(
            KlineEvent(
                exchange="binance",
                market_type=self._config.market_type,
                symbol=symbol,
                timestamp=int(row[0]),
                interval=ivl,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                closed=True,
            )
            for row in rows
        )
        self._buffers[f"{symbol}:{ivl}"] = buf
        logger.info("seeded %d %s candles for %s", len(buf), ivl, symbol)

    # -- reconnect hook -----------------------------------------------------------

    async def _after_ws_connect(self) -> None:
        """Refresh L2 books after any (re)connect so diffs never gap."""
        if self._l2 is None or not self._watched:
            return
        try:
            async with asyncio.timeout(_L2_REINIT_BUDGET_S):
                await self._l2.reinitialize_all()
        except asyncio.TimeoutError:
            logger.warning(
                "post-reconnect L2 re-init timed out (>=%.0fs)", _L2_REINIT_BUDGET_S
            )

    # -- wire handlers -------------------------------------------------------------

    def _claim(
        self,
        event: TickerEvent
        | KlineEvent
        | TradeEvent
        | LiquidationEvent
        | FundingRateEvent
        | OrderBookL2Snapshot,
    ) -> None:
        """Tag every outgoing event with this source's market_type."""
        object.__setattr__(event, "market_type", self._config.market_type)

    def _handle_ticker(self, event: TickerEvent) -> None:
        self._claim(event)
        self._cache.put_ticker(event.symbol, event)
        self._event_bus.publish(event)

    def _handle_kline(self, event: KlineEvent) -> None:
        self._claim(event)
        self._cache.put_kline(event.symbol, event)

        key = f"{event.symbol}:{event.interval}" if event.interval else event.symbol
        buf = self._buffers.get(key)
        if buf is not None:
            if buf and buf[-1].timestamp == event.timestamp:
                buf[-1] = event
            else:
                buf.append(event)
        self._event_bus.publish(event)

    def _handle_trade(self, event: TradeEvent) -> None:
        self._claim(event)
        self._event_bus.publish(event)

    def _handle_liquidation(self, event: LiquidationEvent) -> None:
        self._claim(event)
        self._event_bus.publish(event)

    def _handle_funding(self, event: FundingRateEvent) -> None:
        self._claim(event)
        self._event_bus.publish(event)

    async def _handle_depth_delta(self, delta: BinanceDepthDelta) -> None:
        if self._l2 is None:
            return
        wire_up = delta.delta.symbol.replace("/", "").upper()
        applied = await self._l2.apply_delta(wire_up, delta)
        if not applied:
            return

        sym = delta.delta.symbol
        now = time.monotonic()
        if now - self._last_depth_publish.get(sym, 0.0) < _DEPTH_PUBLISH_GAP_S:
            return
        self._last_depth_publish[sym] = now

        # snapshot assembly sorts both sides and builds pydantic levels per
        # update — kept off the loop thread (was 2-4s API stalls under load).
        snapshot = await asyncio.to_thread(self._l2.get_snapshot, wire_up)
        if snapshot is not None:
            self._claim(snapshot)
            self._snapshots.update(snapshot.symbol, snapshot)
            self._event_bus.publish(snapshot)

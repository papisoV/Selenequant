"""A-share daily kline source via Sina public API.

1d-interval only — paper trading for A-share strategies needs one bar per
trading day, so a poll loop replaces a WebSocket. Sina returns UNADJUSTED
prices while the mining/backtest DB holds qfq data; small jumps around
ex-dividend dates are accepted for paper trading.

Note: push2his.eastmoney.com (akshare's default host) is unstable from some
networks — connection resets on most attempts. Sina's quotes.sina.cn kline
API proved reliable, so it is used directly.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests

from selene_space_market.poll_backoff import PollBackoff
from selene_space_market.source import ExchangeSourceConfig
from selene_space_types.market import KlineEvent

logger = logging.getLogger(__name__)

# China has no DST, so a fixed +08:00 offset is exact. A daily bar is stamped at
# its session date's midnight in Beijing time — the same convention the anomaly
# detector's KlineDB cutoff relies on. Stamping off local wall time instead
# would shift every bar by the host's UTC offset (8h on a UTC host), sliding
# bars across the session-date boundary the detector filters on.
_CST = timezone(timedelta(hours=8))

_SINA_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn",
}
_POLL_INTERVAL_SEC = 600  # check for a new daily bar every 10 minutes
# Failure backoff: 600s → 1200s → 2400s → cap 6000s; any success resets.
# Staleness: warn once when the newest cached bar is older than 4 poll cycles.
_BACKOFF_CAP_SEC = 6000
_STALE_AFTER_SEC = _POLL_INTERVAL_SEC * 4


def _to_sina_symbol(symbol: str) -> str:
    """'000001.SZ' → 'sz000001'; '600519.SH' → 'sh600519'."""
    code, _, suffix = symbol.partition(".")
    prefix = {"SZ": "sz", "SH": "sh"}.get(suffix.upper())
    if not prefix or not code.isdigit() or len(code) != 6:
        raise ValueError(f"Invalid A-share symbol: {symbol!r}")
    return f"{prefix}{code}"


def _parse_rows(rows: list[dict[str, str]], symbol: str) -> list[KlineEvent]:
    """Sina rows ({day, open, high, low, close, volume}) → KlineEvents."""
    events: list[KlineEvent] = []
    for row in rows:
        day = row["day"]
        ts_ms = int(
            datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_CST).timestamp() * 1000
        )
        ohlc = {k: float(row[k]) for k in ("open", "high", "low", "close")}
        # DB importer drops non-positive qfq prices; mirror that guard here
        if any(v <= 0 for v in ohlc.values()):
            continue
        events.append(
            KlineEvent(
                exchange="astock",
                market_type="spot",
                symbol=symbol,
                timestamp=ts_ms,
                interval="1d",
                volume=float(row["volume"]),
                closed=True,
                **ohlc,
            )
        )
    return events


class AstockSource:
    """ExchangeSource adapter for A-share daily bars (Sina, poll-based).

    Only the surface MarketDataService actually uses for kline-driven
    strategies is implemented: config/start/stop/subscribe/kline accessors
    plus fetch_klines_rest for warmup. Ticker/orderbook are not applicable.
    """

    def __init__(self) -> None:
        self._config = ExchangeSourceConfig(exchange="astock", market_type="spot")
        self._running = False
        self._subscribed: dict[str, str] = {}  # symbol → interval
        self._kline_cache: dict[str, KlineEvent] = {}
        self._kline_history: dict[str, list[KlineEvent]] = {}
        self._poll_task: asyncio.Task | None = None
        self._on_kline_cb: Callable[[KlineEvent], Any] | None = None
        self._backoff = PollBackoff(
            _POLL_INTERVAL_SEC,
            cap_sec=_BACKOFF_CAP_SEC,
            stale_after_sec=_STALE_AFTER_SEC,
        )

    @property
    def config(self) -> ExchangeSourceConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self._subscribed:
            self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("AstockSource started (poll interval %ss)", _POLL_INTERVAL_SEC)

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    async def subscribe(
        self,
        symbol: str,
        *,
        kline: bool = True,
        kline_interval: str | None = None,
        **_: Any,
    ) -> None:
        interval = kline_interval or "1d"
        if interval != "1d":
            logger.warning(
                "AstockSource only supports 1d; requested %s — using 1d", interval
            )
        self._subscribed[symbol] = "1d"
        if self._running and self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def unsubscribe(self, symbol: str) -> None:
        self._subscribed.pop(symbol, None)

    def get_ticker(self, symbol: str) -> None:
        return None

    def get_kline(self, symbol: str) -> KlineEvent | None:
        self._backoff.warn_if_stale(symbol, logger, "AstockSource")
        return self._kline_cache.get(symbol)

    def staleness_seconds(self, symbol: str) -> int | None:
        """Seconds since the last successful poll for *symbol* (None = never)."""
        return self._backoff.staleness_seconds(symbol)

    def get_klines(self, symbol: str, limit: int = 100) -> list[KlineEvent]:
        return self._kline_history.get(symbol, [])[-limit:]

    def get_orderbook(self, symbol: str) -> None:
        return None

    def get_symbols(self) -> list[str]:
        return list(self._subscribed)

    def on_ticker(self, callback: Callable[[Any], Any]) -> None:
        pass

    def on_kline(self, callback: Callable[[KlineEvent], Any]) -> None:
        self._on_kline_cb = callback

    def on_orderbook(self, callback: Callable[[Any], Any]) -> None:
        pass

    def _fetch_sina(self, symbol: str, datalen: int) -> list[KlineEvent]:
        sina_sym = _to_sina_symbol(symbol)
        resp = requests.get(
            _SINA_KLINE_URL,
            params={"symbol": sina_sym, "scale": 240, "ma": "no", "datalen": datalen},
            headers=_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        text = resp.text.strip()
        if text in ("", "null"):
            raise ConnectionError(f"Sina kline API returned empty for {symbol}")
        import json

        return _parse_rows(json.loads(text), symbol)

    async def fetch_klines_rest(
        self,
        symbol: str,
        interval: str = "1d",
        limit: int = 200,
        end_time: int | None = None,
        start_time: int | None = None,
    ) -> list[KlineEvent]:
        """Warmup/pagination entry point used by MarketDataService."""
        events = self._fetch_sina(symbol, min(limit, 1023))
        if end_time is not None:
            events = [e for e in events if e.timestamp <= end_time]
        if start_time is not None:
            events = [e for e in events if e.timestamp >= start_time]
        return events[-limit:]

    async def _poll_loop(self) -> None:
        """Poll subscribed symbols for the latest daily bar; publish new ones.

        Failed symbols enter an exponential backoff window (skipped entirely
        while it lasts); a success resets the window.
        """
        last_ts: dict[str, int] = {}
        while self._running:
            for symbol in list(self._subscribed):
                if self._backoff.blocked(symbol):
                    continue
                self._backoff.record_attempt(symbol)
                try:
                    events = await asyncio.to_thread(self._fetch_sina, symbol, 2)
                    if not events:
                        self._backoff.record_failure(symbol)
                        continue
                    self._backoff.record_success(symbol)
                    latest = events[-1]
                    if latest.timestamp <= last_ts.get(symbol, 0):
                        continue
                    last_ts[symbol] = latest.timestamp
                    self._kline_cache[symbol] = latest
                    history = self._kline_history.setdefault(symbol, [])
                    history.append(latest)
                    if len(history) > 300:
                        del history[:-300]
                    if self._on_kline_cb:
                        self._on_kline_cb(latest)
                    logger.info(
                        "AstockSource: new 1d bar %s %s close=%s",
                        symbol,
                        latest.timestamp,
                        latest.close,
                    )
                except Exception:
                    self._backoff.record_failure(symbol)
                    logger.warning(
                        "AstockSource poll failed for %s (consecutive fails: %d, "
                        "backing off %ss)",
                        symbol,
                        self._backoff.fail_count(symbol),
                        self._backoff.backoff_seconds(self._backoff.fail_count(symbol)),
                        exc_info=True,
                    )
            await asyncio.sleep(_POLL_INTERVAL_SEC)

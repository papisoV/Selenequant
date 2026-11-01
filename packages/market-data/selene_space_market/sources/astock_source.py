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
from typing import Any, Callable, cast

import requests

from selene_space_core.session_clock import (
    bar_payload_differs,
    daily_bar_is_final,
    session_date_of,
)
from selene_space_core.sina_symbols import to_sina_symbol
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
    """'000001.SZ' → 'sz000001'; '600519.SH' → 'sh600519'; '920002.BJ' → 'bj920002'.

    Delegates to :mod:`selene_space_core.sina_symbols` — the board rule used to
    live here as a two-board ``{SZ, SH}`` map, which raised on every Beijing
    code. It lives in ``core`` because the A-share package needs the same rule
    and does not depend on ``market-data``. Kept as a thin alias so existing
    import sites and their tests stay valid.
    """
    # cast: ``selene_space_core`` ships no ``py.typed``, so mypy sees every
    # cross-package call as Any. The function itself is checked in the same
    # run and returns str; this keeps the declared return type honest.
    return cast("str", to_sina_symbol(symbol))


def _is_publishable(previous: KlineEvent | None, latest: KlineEvent) -> bool:
    """Whether ``latest`` carries news relative to ``previous``.

    Two things count as news. A newer session always does. A restatement of the
    *same* session does when any of its numbers moved — which is exactly how the
    intraday snapshot becomes the close, and how a late correction reaches
    subscribers. An unchanged repoll is not news.

    Handing the published bar back to the store is what makes the store's
    upsert (keyed on the full partition tuple, session date included) overwrite
    the snapshot instead of skipping it as "not newer".
    """
    if previous is None:
        return True
    if latest.timestamp > previous.timestamp:
        return True
    if latest.timestamp < previous.timestamp:
        return False
    return bar_payload_differs(previous.model_dump(), latest.model_dump())


def _parse_rows(
    rows: list[dict[str, str]], symbol: str, *, as_of: datetime | None = None
) -> list[KlineEvent]:
    """Sina rows ({day, open, high, low, close, volume}) → KlineEvents.

    ``as_of`` is when the response arrived; it decides whether a bar stamped for
    *today* is the session's close or a still-forming snapshot. Sina's daily
    endpoint returns a row for today from the opening bell — price so far,
    volume so far — and the payload carries nothing to tell that row from
    yesterday's final one. Marking it ``closed=True`` (which this parser used to
    do unconditionally) both let strategy runtimes act on a half-formed bar and,
    through ``sync_incremental``'s "strictly newer timestamp" filter, froze the
    snapshot in the store forever: the restated row after the close carries the
    same session date and was therefore discarded.
    """
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
                closed=daily_bar_is_final(session_date_of(ts_ms), as_of=as_of),
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
        """Poll subscribed symbols for the latest daily bar; publish changes.

        Failed symbols enter an exponential backoff window (skipped entirely
        while it lasts); a success resets the window.

        A daily bar is *restated*, not appended: the row for today changes from
        an intraday snapshot to the session's close under the same session-date
        timestamp. Deduping on the timestamp alone therefore swallowed exactly
        the update that matters — the final bar never reached subscribers, and
        the snapshot stayed the last word for the day. The guard below compares
        the payload as well, so a restatement or a late correction is published
        while an unchanged repoll is still dropped.
        """
        last_published: dict[str, KlineEvent] = {}
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
                    if not _is_publishable(last_published.get(symbol), latest):
                        continue
                    last_published[symbol] = latest
                    self._kline_cache[symbol] = latest
                    history = self._kline_history.setdefault(symbol, [])
                    if history and history[-1].timestamp == latest.timestamp:
                        # Same session restated — replace the stale row rather
                        # than stacking two versions of one session in history,
                        # which would double-count it in any windowed read.
                        history[-1] = latest
                    else:
                        history.append(latest)
                    if len(history) > 300:
                        del history[:-300]
                    if self._on_kline_cb:
                        self._on_kline_cb(latest)
                    logger.info(
                        "AstockSource: %s 1d bar %s close=%s%s",
                        "final" if latest.closed else "snapshot",
                        symbol,
                        latest.close,
                        "" if latest.closed else " (session still forming)",
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

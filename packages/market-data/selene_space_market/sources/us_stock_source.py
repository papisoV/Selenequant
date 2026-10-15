"""US stock kline source via Yahoo v8 chart API (poll-based, no WS).

Caliber decision (plan us-stock-data-line-plan-1001 OQ-1, a
partition-immutability promise): adjclose ratio scaling — r =
adjclose[i]/close[i], OHL×r, close=adjclose, volume untouched (yfinance
``auto_adjust`` equivalent). The mode is recorded per-partition in
``data/us_stock_adjust_mode.json`` (best-effort sidecar; klines.db has no
caliber column). Changing it requires ``KlineDB.delete_partition`` + resync.

Yahoo restates historical bars on split events, and ``sync_incremental``
appends only newer timestamps, so an old partition keeps its pre-split
vintage (a 4x fake jump inside the partition). The sentinel warns when an
adjacent-bar |log return| > 0.35 coincides with a volume blowout — recovery
is manual ``delete_partition`` + resync.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from selene_space_core.resilience import OutboundGateway
from selene_space_market.poll_backoff import PollBackoff
from selene_space_market.source import ExchangeSourceConfig
from selene_space_types.market import KlineEvent

logger = logging.getLogger(__name__)

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_POLL_INTERVAL_SEC = 60  # flat poll, request rate is negligible (OQ-4)
# Failure backoff: 60s → 120s → 240s → cap 600s; any success resets.
# Staleness: warn once when the newest cached bar is older than 5 poll cycles.
_BACKOFF_CAP_SEC = 600
_STALE_AFTER_SEC = _POLL_INTERVAL_SEC * 5
_ADJUST_MODE = "adjclose_ratio"
_SPLIT_LOG_RETURN = 0.35  # ≈42% single-bar jump
_VOLUME_BLOWOUT = 5.0
_HISTORY_CAP = 300
_ADJUST_SIDECAR = "us_stock_adjust_mode.json"

# canonical interval → Yahoo interval label. No native 4h on Yahoo (OQ-5):
# "4h" requests fall back to 1h with a loud warning; the chart page
# aggregates 4h itself (chart.py _aggregate_4h).
_YAHOO_INTERVALS = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "1d": "1d",
    "1w": "1wk",
}
_INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


def _normalize_symbol(symbol: str) -> str:
    """'brk.a' / 'brk$a' → 'BRK-A' (Yahoo notation, AC3)."""
    sym = symbol.strip().upper().replace(".", "-").replace("$", "-")
    if not re.fullmatch(r"[A-Z0-9-]{1,10}", sym):
        raise ValueError(f"Invalid US stock symbol: {symbol!r}")
    return sym


def _resolve_interval(interval: str) -> str:
    """Canonical event interval (AC4: events always carry the canonical label)."""
    ivl = interval.strip().lower()
    if ivl == "4h":
        logger.warning(
            "USStockSource: Yahoo has no native 4h bars — falling back to 1h "
            "(the chart page aggregates 4h itself)"
        )
        return "1h"
    if ivl not in _YAHOO_INTERVALS:
        logger.warning("USStockSource: unsupported interval %r — using 1d", interval)
        return "1d"
    return ivl


def _parse_chart(
    payload: dict[str, Any],
    symbol: str,
    interval: str,
) -> list[KlineEvent]:
    """v8 chart payload → KlineEvents with adjclose ratio scaling (O-A).

    Mirrors us_radar.py:_bars_from_chart: skip bars with a None close and
    fall back open/high/low → close when Yahoo omits them.
    """
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return []
    res = result[0]
    ts = res.get("timestamp") or []
    indicators = res.get("indicators") or {}
    quote_list = indicators.get("quote") or []
    if not ts or not quote_list:
        return []
    quote = quote_list[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    adjcloses = ((indicators.get("adjclose") or [{}])[0] or {}).get("adjclose") or []

    events: list[KlineEvent] = []
    for i, ts_s in enumerate(ts):
        close = closes[i] if i < len(closes) else None
        if close is None or close <= 0:
            continue
        adj = adjcloses[i] if i < len(adjcloses) else None
        op = opens[i] if i < len(opens) else None
        hi = highs[i] if i < len(highs) else None
        lo = lows[i] if i < len(lows) else None
        vol = volumes[i] if i < len(volumes) else 0.0
        adj = close if adj is None else adj
        op = adj if op is None else op
        hi = adj if hi is None else hi
        lo = adj if lo is None else lo
        r = adj / close
        op, hi, lo = op * r, hi * r, lo * r
        if min(op, hi, lo, adj) <= 0:  # astock_source.py:54-55 same guard
            continue
        events.append(
            KlineEvent(
                exchange="yahoo",
                market_type="us_stock",
                symbol=symbol,
                timestamp=int(ts_s) * 1000,
                interval=interval,
                open=op,
                high=hi,
                low=lo,
                close=adj,
                volume=float(vol or 0.0),
                closed=True,
            )
        )
    return events


def _check_split_restatement(
    events: list[KlineEvent], label: str, prev_close: float | None
) -> None:
    """Loud warning when a bar-to-bar close jump looks like split restatement.

    Heuristic only: adjacent 1d/1w bars with |log return| > 0.35 plus a
    volume blowout. The old partition's caliber is rotten at that point —
    recovery is ``KlineDB.delete_partition`` + resync (AC11).
    """
    if not events or events[0].interval not in ("1d", "1w"):
        return
    vols = sorted(e.volume for e in events)
    # lower median — including the suspect bar in an upper-median pick would
    # hide short series (2 bars: upper median IS the suspect's volume)
    median = vols[(len(vols) - 1) // 2] if vols else 0.0
    anchor = prev_close
    for e in events:
        if (
            anchor is not None
            and anchor > 0
            and e.close > 0
            and abs(math.log(e.close / anchor)) > _SPLIT_LOG_RETURN
            and median > 0
            and e.volume > _VOLUME_BLOWOUT * median
        ):
            logger.warning(
                "USStockSource[%s]: suspected split restatement at ts=%s "
                "(|log return| > %.2f with %sx volume blowout). The existing "
                "partition mixes pre-split vintage — delete_partition"
                "('yahoo','us_stock',symbol,interval) then resync "
                "(KlineDB.delete_partition)",
                label,
                e.timestamp,
                _SPLIT_LOG_RETURN,
                _VOLUME_BLOWOUT,
            )
        anchor = e.close


class USStockSource:
    """ExchangeSource adapter for US stock bars (Yahoo v8 chart, poll-based).

    Minimal surface like AstockSource: only what MarketDataService actually
    uses for kline-driven strategies. Ticker/orderbook are not applicable
    for a poll-based EOD-ish flow.
    """

    def __init__(self, proxy_url: str | None = None) -> None:
        self._config = ExchangeSourceConfig(exchange="yahoo", market_type="us_stock")
        self._http = OutboundGateway(
            proxy=proxy_url or None,
            default_timeout=httpx.Timeout(15.0, connect=10.0),
            default_circuit=True,  # per-host breaker, okx_source.py:117 precedent
        )
        self._running = False
        self._subscribed: dict[str, str] = {}  # symbol → canonical interval
        self._kline_cache: dict[str, KlineEvent] = {}  # "SYM:ivl" → event
        self._kline_history: dict[str, list[KlineEvent]] = {}
        self._last_close: dict[str, float] = {}  # sentinel cross-fetch anchor
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
        logger.info("USStockSource started (poll interval %ss)", _POLL_INTERVAL_SEC)

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
        self._subscribed[symbol] = _resolve_interval(kline_interval or "1d")
        if self._running and self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def unsubscribe(self, symbol: str, kline_interval: str | None = None) -> None:
        self._subscribed.pop(symbol, None)

    def get_ticker(self, symbol: str) -> None:
        return None

    def get_kline(self, symbol: str) -> KlineEvent | None:
        ivl = self._subscribed.get(symbol)
        if ivl is None:
            return None
        key = f"{symbol}:{ivl}"
        self._backoff.warn_if_stale(key, logger, "USStockSource")
        return self._kline_cache.get(key)

    def staleness_seconds(self, symbol: str) -> int | None:
        """Seconds since the last successful poll for *symbol* (None = never)."""
        ivl = self._subscribed.get(symbol)
        if ivl is None:
            return None
        return self._backoff.staleness_seconds(f"{symbol}:{ivl}")

    def get_klines(self, symbol: str, limit: int = 100) -> list[KlineEvent]:
        ivl = self._subscribed.get(symbol)
        if ivl is None:
            return []
        return self._kline_history.get(f"{symbol}:{ivl}", [])[-limit:]

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

    def _record_adjust_mode(self, symbol: str, interval: str) -> None:
        """Caliber guard (plan R3): record the partition's adjust mode in a
        sidecar JSON; a mismatch means the partition mixes calibers."""
        try:
            data_dir = os.environ.get("SELENE_DATA_DIR", "data")
            os.makedirs(data_dir, exist_ok=True)
            path = os.path.join(data_dir, _ADJUST_SIDECAR)
            record: dict[str, str] = {}
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    record = json.load(f)
            key = f"yahoo:us_stock:{symbol}:{interval}"
            existing = record.get(key)
            if existing and existing != _ADJUST_MODE:
                logger.warning(
                    "USStockSource: partition %s was built with adjust_mode=%r "
                    "but this source emits %r — mixed calibers. delete_partition "
                    "+ resync required.",
                    key,
                    existing,
                    _ADJUST_MODE,
                )
            if existing != _ADJUST_MODE:
                record[key] = _ADJUST_MODE
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(record, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.debug("adjust-mode sidecar update skipped", exc_info=True)

    async def fetch_klines_rest(
        self,
        symbol: str,
        interval: str = "1d",
        limit: int = 200,
        end_time: int | None = None,
        start_time: int | None = None,
    ) -> list[KlineEvent]:
        """Warmup/pagination entry point used by MarketDataService.

        ms epochs → period1/period2 seconds (AC5). Deep history for intraday
        is bounded by Yahoo per-request caps (1m ≈ 7d) — the caller
        (paginate_backwards) walks the end_time cursor and stops on an empty
        page, which is the upstream limit, not a bug.
        """
        ivl = _resolve_interval(interval)
        try:
            sym = _normalize_symbol(symbol)
            period2 = int(
                (end_time if end_time is not None else time.time() * 1000) // 1000
            )
            if start_time is not None:
                period1 = int(start_time // 1000)
            else:
                # ×3 buffer so gap-heavy trading calendars still yield ~limit bars
                step_ms = _INTERVAL_MS.get(ivl, 86_400_000)
                period1 = period2 - (limit * step_ms * 3) // 1000
            resp = await self._http.get(
                _CHART_URL.format(symbol=sym),
                params={
                    "interval": _YAHOO_INTERVALS[ivl],
                    "period1": str(period1),
                    "period2": str(period2),
                },
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            payload = resp.json()
        except ValueError:
            raise
        except Exception:
            logger.warning("Yahoo chart fetch failed %s %s", symbol, ivl, exc_info=True)
            return []

        events = _parse_chart(payload, sym, ivl)
        self._record_adjust_mode(sym, ivl)
        anchor_key = f"{sym}:{ivl}"
        _check_split_restatement(events, anchor_key, self._last_close.get(anchor_key))
        if events:
            self._last_close[anchor_key] = events[-1].close
        return events[-limit:]

    async def _poll_loop(self) -> None:
        """Poll subscribed symbols for their latest bar; publish new ones.

        Failed symbols enter an exponential backoff window (skipped entirely
        while it lasts); a success resets the window.
        """
        last_ts: dict[str, int] = {}
        while self._running:
            for symbol, ivl in list(self._subscribed.items()):
                key = f"{symbol}:{ivl}"
                if self._backoff.blocked(key):
                    continue
                self._backoff.record_attempt(key)
                try:
                    events = await self.fetch_klines_rest(symbol, ivl, limit=2)
                    if not events:
                        self._backoff.record_failure(key)
                        continue
                    self._backoff.record_success(key)
                    latest = events[-1]
                    if latest.timestamp <= last_ts.get(key, 0):
                        continue
                    last_ts[key] = latest.timestamp
                    self._kline_cache[key] = latest
                    history = self._kline_history.setdefault(key, [])
                    history.append(latest)
                    if len(history) > _HISTORY_CAP:
                        del history[:-_HISTORY_CAP]
                    if self._on_kline_cb:
                        self._on_kline_cb(latest)
                    logger.info(
                        "USStockSource: new %s bar %s ts=%s close=%s",
                        ivl,
                        symbol,
                        latest.timestamp,
                        latest.close,
                    )
                except Exception:
                    self._backoff.record_failure(key)
                    logger.warning(
                        "USStockSource poll failed for %s (consecutive fails: %d, "
                        "backing off %ss)",
                        key,
                        self._backoff.fail_count(key),
                        self._backoff.backoff_seconds(self._backoff.fail_count(key)),
                        exc_info=True,
                    )
            await asyncio.sleep(_POLL_INTERVAL_SEC)

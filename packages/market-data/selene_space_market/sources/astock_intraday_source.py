"""A-share intraday (L1) snapshot source via Sina's public realtime quote API.

Companion to :mod:`selene_space_market.sources.astock_source` (daily kline,
Sina ``getKLineData``). This one covers the intraday plane: a ~3s poll over
``hq.sinajs.cn/list=`` returning one ``TickerEvent`` per symbol per update.
It registers as ``("astock", "intraday")`` so it coexists with the daily
source's ``("astock", "spot")`` key instead of replacing it.

Payload shape (GB18030, one ``var hq_str_<code>="...";`` line per symbol)::

    0 name   1 open   2 prev_close   3 last   4 high   5 low
    6 bid1   7 ask1   8 volume(shares)   9 amount(CNY)
    10..19 buy1..5 (vol, price)   20..29 sell1..5 (vol, price)
    30 date  31 time  32 status

Only the ticker plane is surfaced. The five depth levels ride along in the
same payload but are deliberately not emitted: A-share Level-2 is out of
scope for this source (它是作战层里价值最低的一项,先不做), and the
orderbook callback in ``MarketDataService`` feeds a crypto-shaped detector
that A-share five-level data would only pollute.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from selene_space_market.poll_backoff import PollBackoff
from selene_space_market.source import ExchangeSourceConfig
from selene_space_market.sources.astock_outage_tracker import (
    OutageEpisode,
    OutageTracker,
)
from selene_space_market.sources.astock_source import _to_sina_symbol
from selene_space_types.market import KlineEvent, OrderBookL2Snapshot, TickerEvent

logger = logging.getLogger(__name__)

_SINA_HQ_URL = "https://hq.sinajs.cn/list={codes}"
# Sina rejects requests without a finance.sina.com.cn referer (403 Forbidden).
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn",
}
# China has no DST, so a fixed +08:00 offset is exact — same convention as
# `ashare_engine.py` (`_CST`) and avoids depending on the IANA tz database.
_CST = timezone(timedelta(hours=8))

_POLL_INTERVAL_SEC = 3.0
_OFF_HOURS_INTERVAL_SEC = 60.0
_BACKOFF_CAP_SEC = 300.0
_STALE_AFTER_SEC = 60.0
# Sina accepts long symbol lists, but keep the URL comfortably under any
# proxy/edge limit (80 codes ≈ 800 chars).
_BATCH_SIZE = 80

# 09:15 covers the opening call-auction window; 15:05 covers the close print.
_SESSION_START_MIN = 9 * 60 + 15
_SESSION_END_MIN = 15 * 60 + 5

_LINE_RE = re.compile(r'hq_str_([a-z]{2}\d{6})="([^"]*)"')
_MIN_FIELDS = 32  # need up to the time field (index 31)


def _sina_code_to_symbol(code: str) -> str:
    """'sh600519' → '600519.SH' (inverse of ``_to_sina_symbol``)."""
    return f"{code[2:]}.{code[:2].upper()}"


def _now_ms() -> int:
    """Wall-clock epoch milliseconds (outage episodes are wall-clock records)."""
    return int(time.time() * 1000)


def _in_l1_window(now: datetime) -> bool:
    """True during A-share cash-session hours on a weekday.

    Holiday-unaware on purpose: this only throttles polling, and a closed
    session simply yields no advancing timestamps to publish. Avoids pulling
    the exchange calendar (a cross-package dependency) into this source.
    """
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return _SESSION_START_MIN <= minutes <= _SESSION_END_MIN


def _to_epoch_ms(date_str: str, time_str: str) -> int:
    """'2026-10-13' + '15:34:59' (Asia/Shanghai) → epoch milliseconds."""
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    return int(dt.replace(tzinfo=_CST).timestamp() * 1000)


def _parse_hq_line(line: str) -> tuple[str, list[str]] | None:
    """One ``var hq_str_sh600519="...";`` line → (code, comma fields).

    Returns None for a malformed line or an empty payload (Sina answers
    ``var hq_str_badcode="";`` for unknown codes).
    """
    match = _LINE_RE.search(line)
    if match is None:
        return None
    code, payload = match.group(1), match.group(2)
    if not payload:
        return None
    return code, payload.split(",")


def _parse_ticker(code: str, fields: list[str]) -> TickerEvent | None:
    """Fields for one symbol → TickerEvent.

    Returns None when the payload is well-formed but the symbol has no
    tradeable quote *yet* — pre-open / halted, where Sina publishes ``0`` in
    the price slot. That is a healthy upstream, not a failure, so callers must
    not back off on it. Raises ``ValueError`` for a malformed payload (short,
    or a non-numeric field), which callers treat as an upstream problem.
    """
    if len(fields) < _MIN_FIELDS:
        raise ValueError(f"short payload: {len(fields)} fields")
    prev_close = float(fields[2])
    last = float(fields[3])
    bid = float(fields[6])
    ask = float(fields[7])
    volume = float(fields[8])
    ts = _to_epoch_ms(fields[30], fields[31])
    if last <= 0:
        # Pre-open / halted: Sina publishes 0 in the price slot.
        return None
    change_pct = (last - prev_close) / prev_close * 100.0 if prev_close > 0 else 0.0
    return TickerEvent(
        exchange="astock",
        symbol=_sina_code_to_symbol(code),
        timestamp=ts,
        last_price=last,
        bid=bid,
        ask=ask,
        # Sina's field 8 is today's cumulative traded shares, not a 24h
        # rolling window; it is the closest available analogue.
        volume_24h=volume,
        change_24h_pct=change_pct,
        # Field 0 is the venue-provided display name (may be "ST ..."/"退 ...",
        # which the price-limit rules read to pick the ST ratio).
        name=fields[0],
        # Field 2 is the exchange's official previous close — the raw, current
        # basis limit prices are computed from. Surfaced so the anomaly detector
        # can use it directly instead of a daily-bar store (stale qfq import).
        prev_close=prev_close if prev_close > 0 else None,
    )


def parse_hq_response(text: str) -> tuple[dict[str, TickerEvent], set[str]]:
    """Whole response body → (quotable tickers, seen-but-not-quotable symbols).

    ``seen`` holds symbols Sina answered with a well-formed payload that is
    simply not quotable *yet* (pre-open / halted). They confirm a healthy
    upstream, so the poll loop keeps them out of backoff — otherwise every
    subscribed symbol would accumulate a failure window during the opening
    call auction and could still be blocked when the auction finally prints.
    Malformed lines (short payload, bad number) fall into neither bucket.
    """
    tickers: dict[str, TickerEvent] = {}
    seen: set[str] = set()
    for line in text.splitlines():
        parsed = _parse_hq_line(line)
        if parsed is None:
            continue
        code, fields = parsed
        try:
            event = _parse_ticker(code, fields)
        except ValueError:
            logger.debug("AstockIntradaySource: malformed payload for %s", code)
            continue
        if event is not None:
            tickers[event.symbol] = event
        else:
            seen.add(_sina_code_to_symbol(code))
    return tickers, seen


class AstockIntradaySource:
    """ExchangeSource adapter for A-share intraday L1 snapshots (Sina poll).

    Implements the full :class:`~selene_space_market.source.ExchangeSource`
    surface so it registers without a cast, but only the ticker plane is
    live: kline/orderbook accessors are deliberate no-ops (see module
    docstring). Failures reuse :class:`PollBackoff` per symbol, so a symbol
    that errors sits out its exponential window while healthy symbols keep
    polling.
    """

    def __init__(
        self,
        *,
        poll_interval_sec: float = _POLL_INTERVAL_SEC,
        off_hours_interval_sec: float = _OFF_HOURS_INTERVAL_SEC,
        batch_size: int = _BATCH_SIZE,
    ) -> None:
        self._config = ExchangeSourceConfig(exchange="astock", market_type="intraday")
        self._running = False
        self._subscribed: set[str] = set()
        self._ticker_cache: dict[str, TickerEvent] = {}
        self._last_published: dict[str, tuple[int, float, float, float, float]] = {}
        self._empty_response_warned = False
        self._poll_task: asyncio.Task[None] | None = None
        self._on_ticker_cb: Callable[[TickerEvent], Any] | None = None
        self._poll_interval = poll_interval_sec
        self._off_hours_interval = off_hours_interval_sec
        self._batch_size = max(1, batch_size)
        self._backoff = PollBackoff(
            poll_interval_sec,
            cap_sec=_BACKOFF_CAP_SEC,
            stale_after_sec=_STALE_AFTER_SEC,
        )
        # S2 outage bookkeeping. The tracker is pure and no-raise; the sink is
        # attached later (after the durable store exists) via `set_gap_sink`,
        # which is why it is not a constructor argument.
        self._outage_tracker = OutageTracker(source="astock_intraday")
        self._gap_sink: Callable[[OutageEpisode], None] | None = None
        # Set on the first publish after an outage closes, so the stale-timestamp
        # warning fires once per episode rather than once per symbol.
        self._pending_freshness_check = False

    # -- ExchangeSource: identity / lifecycle ------------------------------

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
        if self._subscribed and self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "AstockIntradaySource started (poll %ss in-session / %ss off-hours)",
            self._poll_interval,
            self._off_hours_interval,
        )

    async def stop(self) -> None:
        self._running = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None

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
        # Validate eagerly so an unsupported code fails at wiring time rather
        # than inside the poll loop's batch request.
        _to_sina_symbol(symbol)
        self._subscribed.add(symbol)
        if self._running and self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def unsubscribe(self, symbol: str, kline_interval: str | None = None) -> None:
        self._subscribed.discard(symbol)

    # -- ExchangeSource: poll-style reads ----------------------------------

    def get_ticker(self, symbol: str) -> TickerEvent | None:
        self._backoff.warn_if_stale(symbol, logger, "AstockIntradaySource")
        return self._ticker_cache.get(symbol)

    def get_kline(self, symbol: str) -> KlineEvent | None:
        return None

    def get_klines(self, symbol: str, limit: int = 100) -> list[KlineEvent]:
        return []

    def get_orderbook(self, symbol: str) -> OrderBookL2Snapshot | None:
        return None

    def get_symbols(self) -> list[str]:
        return sorted(self._subscribed)

    def staleness_seconds(self, symbol: str) -> int | None:
        """Seconds since the last successful poll for *symbol* (None = never)."""
        return self._backoff.staleness_seconds(symbol)

    # -- ExchangeSource: push wiring ---------------------------------------

    def on_ticker(self, callback: Callable[[TickerEvent], Any]) -> None:
        self._on_ticker_cb = callback

    def on_kline(self, callback: Callable[[KlineEvent], Any]) -> None:
        pass

    def on_orderbook(self, callback: Callable[[OrderBookL2Snapshot], Any]) -> None:
        pass

    # -- S2 outage sink ----------------------------------------------------

    def set_gap_sink(self, sink: Callable[[OutageEpisode], None] | None) -> None:
        """Attach the sink that persists outage episodes (called twice per
        episode: once at open, once at close).

        Set *after* construction because the durable store does not exist yet
        when the source is registered. A sink that raises is caught and logged
        — outage bookkeeping must never take down the poll loop.
        """
        self._gap_sink = sink

    def _emit_gap(self, episode: OutageEpisode) -> None:
        if self._gap_sink is None:
            return
        try:
            self._gap_sink(episode)
        except Exception:
            logger.exception(
                "AstockIntradaySource: gap sink failed for outage opened at %dms",
                episode.opened_at_ms,
            )

    # -- REST fallback / warmup --------------------------------------------

    async def fetch_tickers_rest(self, symbols: list[str]) -> dict[str, TickerEvent]:
        """One-shot batched fetch, used for warmup and by tests."""
        tickers, _ = await asyncio.to_thread(self._fetch, symbols)
        return tickers

    # -- internals ----------------------------------------------------------

    def _fetch(self, symbols: list[str]) -> tuple[dict[str, TickerEvent], set[str]]:
        codes = ",".join(_to_sina_symbol(s) for s in symbols)
        resp = requests.get(
            _SINA_HQ_URL.format(codes=codes), headers=_HEADERS, timeout=10
        )
        resp.raise_for_status()
        # Sina declares charset=GB18030; pin it rather than trust detection
        # (a mis-decode would corrupt the name field and any future parsing).
        resp.encoding = "gb18030"
        return parse_hq_response(resp.text)

    def _interval_for_now(self) -> float:
        return (
            self._poll_interval
            if _in_l1_window(datetime.now(_CST))
            else self._off_hours_interval
        )

    def _publish(self, event: TickerEvent) -> None:
        # A snapshot is idempotent: republish only when the quote actually
        # moved. The timestamp alone is not enough — bid/ask can shift on
        # order-book churn with no trade (the call-auction indicative quote is
        # exactly this), so the whole published tuple forms the key. Volume is
        # in the key too: a trade at an unchanged price (and unchanged book)
        # still moves today's cumulative volume, and dropping it would lose
        # the tick. Off-hours the tuple is frozen, so a closed session costs
        # requests but emits no events.
        key = (
            event.timestamp,
            event.last_price,
            event.bid,
            event.ask,
            event.volume_24h,
        )
        if self._last_published.get(event.symbol) == key:
            return
        self._last_published[event.symbol] = key
        self._ticker_cache[event.symbol] = event
        self._check_freshness_after_outage(event)
        if self._on_ticker_cb is None:
            return
        # The callback is `event_bus.publish`, which runs every subscriber
        # synchronously in this task. A subscriber raising must not kill the
        # poll loop (the fetch itself succeeded — the symbol is healthy, so it
        # is deliberately not marked failed either).
        try:
            self._on_ticker_cb(event)
        except Exception:
            logger.exception(
                "AstockIntradaySource: ticker callback failed for %s", event.symbol
            )

    def _check_freshness_after_outage(self, event: TickerEvent) -> None:
        """On the first publish after an outage, warn once if the venue
        timestamp lags wall clock — "reconnected, but Sina is serving stale
        snapshots" is otherwise indistinguishable from a healthy feed.

        Once per episode (an 80-symbol batch must not emit 80 lines).
        """
        if not self._pending_freshness_check:
            return
        self._pending_freshness_check = False
        lag_sec = time.time() - event.timestamp / 1000.0
        if lag_sec <= _STALE_AFTER_SEC:
            return
        logger.warning(
            "AstockIntradaySource: recovered from outage but %s quote is stale "
            "(venue timestamp %.0fs behind wall clock, threshold %ss) — upstream "
            "may still be degraded",
            event.symbol,
            lag_sec,
            _STALE_AFTER_SEC,
        )

    def _record_outage_failure(self, chunk: list[str]) -> None:
        """Open or extend an outage episode; emit the episode on first failure."""
        episode = self._outage_tracker.record_batch_failure(chunk, now_ms=_now_ms())
        if episode is None:
            return
        logger.warning(
            "AstockIntradaySource: outage opened at %dms covering %d symbol(s)",
            episode.opened_at_ms,
            len(episode.symbols_affected),
        )
        self._emit_gap(episode)

    def _record_outage_success(
        self,
        chunk: list[str],
        healthy: bool,
        tickers: dict[str, TickerEvent],
    ) -> None:
        """Close an open episode on a genuinely healthy fetch.

        ``healthy`` is the payload verdict (``tickers or seen``): an empty 200
        is a format break, not recovery, and must not close the episode.

        ``tickers`` supplies the post-gap volume bracket (Option A′) and
        refreshes the tracker's running per-symbol volume map.

        ``chunk`` is one batch (≤``_batch_size`` symbols), not the whole
        subscription: a wide subscription recovers chunk by chunk, and the first
        healthy chunk closes the episode. The bracket therefore covers that
        chunk's symbols — ``volume_symbols`` on the episode says exactly how
        many, so a partial bracket is legible rather than silently read as the
        whole book.
        """
        if not healthy:
            return
        volumes = {symbol: event.volume_24h for symbol, event in tickers.items()}
        episode = self._outage_tracker.record_batch_success(
            chunk, now_ms=_now_ms(), healthy=True, volumes=volumes
        )
        if episode is None:
            return
        logger.warning(
            "AstockIntradaySource: outage closed (%dms → %dms, %.0fs, %d batch(es), "
            "volume across gap %s over %d symbol(s))",
            episode.opened_at_ms,
            episode.closed_at_ms,
            episode.duration_sec or 0.0,
            episode.failed_batches,
            (
                f"{episode.volume_delta:.0f}"
                if episode.volume_delta is not None
                else "unknown"
            ),
            episode.volume_symbols,
        )
        self._emit_gap(episode)
        # Arm the freshness guard for the next publish out of this outage.
        self._pending_freshness_check = True

    def _note_fetch_outcome(
        self,
        chunk: list[str],
        tickers: dict[str, TickerEvent],
        seen: set[str],
    ) -> None:
        """Warn once per episode when a batch succeeds but yields no payload.

        A 200 response whose body no longer parses (Sina changing its payload
        shape) otherwise degrades silently: every symbol logs a per-symbol
        miss and backs off, which looks identical to unknown codes. Reporting
        the batch-level signal separates "upstream format broke" from "these
        symbols are not real".

        ``seen`` counts as payload. During the opening call auction every
        symbol is well-formed but not quotable yet, so ``tickers`` is empty
        while the upstream is perfectly healthy — warning there would cry wolf
        at the exact moment the source is most likely to be watched.
        """
        if tickers or seen or not chunk:
            self._empty_response_warned = False
            return
        if self._empty_response_warned:
            return
        self._empty_response_warned = True
        logger.warning(
            "AstockIntradaySource: upstream returned no quotes for %d symbol(s) "
            "— the response format may have changed",
            len(chunk),
        )

    async def _poll_loop(self) -> None:
        try:
            while self._running:
                live = [
                    s for s in sorted(self._subscribed) if not self._backoff.blocked(s)
                ]
                for start in range(0, len(live), self._batch_size):
                    if not self._running:
                        break
                    chunk = live[start : start + self._batch_size]
                    for symbol in chunk:
                        self._backoff.record_attempt(symbol)
                    try:
                        tickers, seen = await asyncio.to_thread(self._fetch, chunk)
                    except Exception:
                        # Transport/HTTP failure for the whole batch — every
                        # symbol in it backs off, so a downed upstream is
                        # retried at most once per window.
                        for symbol in chunk:
                            self._backoff.record_failure(symbol)
                        self._record_outage_failure(chunk)
                        logger.warning(
                            "AstockIntradaySource poll failed for %d symbol(s)",
                            len(chunk),
                            exc_info=True,
                        )
                        continue
                    self._note_fetch_outcome(chunk, tickers, seen)
                    self._record_outage_success(chunk, bool(tickers or seen), tickers)
                    for symbol in chunk:
                        event = tickers.get(symbol)
                        if event is not None:
                            self._backoff.record_success(symbol)
                            self._publish(event)
                        elif symbol in seen:
                            # Well-formed but not quotable yet (pre-open/halted):
                            # upstream is healthy, keep the symbol out of backoff
                            # so it is not still blocked when the auction prints.
                            self._backoff.record_success(symbol)
                        else:
                            # Absent from a 200 response: unknown/delisted code
                            # or a truncated body. Treat as a miss and back off,
                            # but surface the first failure of each episode so a
                            # persistently mistyped symbol is diagnosable.
                            if self._backoff.fail_count(symbol) == 0:
                                logger.warning(
                                    "AstockIntradaySource: no quote for %s "
                                    "(unknown/delisted code or truncated response)",
                                    symbol,
                                )
                            self._backoff.record_failure(symbol)
                await asyncio.sleep(self._interval_for_now())
        finally:
            # The loop can exit on cancellation (`stop`) or on an exception
            # that slips past the per-batch guards. Clearing the handle in both
            # cases is what lets `start()`/`subscribe()` respawn a crashed
            # loop — they only create a task when `_poll_task is None`.
            if self._poll_task is asyncio.current_task():
                self._poll_task = None

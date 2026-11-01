"""A-share full-market snapshot source (whole-market cross-section, Sina poll).

Companion to :mod:`selene_space_market.sources.astock_intraday_source` — that
one is *subscription-scoped* (it polls only the symbols handed to
``subscribe``), which is the wrong shape for battle-desk statistics: ladder
breadth, sentiment phase and board rotation are all **whole-market** quantities
and cannot be derived from a handful of watched names. This source polls the
entire A-share universe in one cross-section per cycle so those consumers have
an input at all.

It registers as ``("astock", "market_snapshot")``, coexisting with the daily
``("astock", "spot")`` and the subscription ``("astock", "intraday")`` keys.

Pull-only by contract: the service's ``register_source`` unconditionally wires
``on_ticker`` to ``event_bus.publish``, so a source that published a
whole-market cross-section (~5500 rows every few seconds) would flood the
shared bus the anomaly detector reads. This source therefore never invokes its
ticker callback — results live in an internal map and are read via
:meth:`AstockMarketSnapshotSource.get_market_rows`.

Payload shape (GB18030, one ``var hq_str_<prefix><code>="...";`` line per
symbol) matches the intraday source::

    0 name   1 open   2 prev_close   3 last   4 high   5 low
    6 bid1   7 ask1   8 volume(shares)   9 amount(CNY)
    ...      30 date  31 time  32 status

Unlike the intraday source this one keeps the whole row (open/high/low included)
because the ladder cross-section folds ``high`` (炸板) as well as ``close``
(封板) against the limit price — ``TickerEvent`` carries neither.

When a whole Sina cycle comes back empty the source degrades to 腾讯
``qt.gtimg.cn`` for that cycle (logged once per outage). 腾讯 answers the same
universe, but its payload is a different format with different units — see
:func:`parse_tencent_response`, whose docstring records the two traps (volume
unit is board-dependent; a halted row is signalled by zero volume, not a zero
price).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import requests

from selene_space_core.sina_symbols import code_to_symbol as _core_code_to_symbol
from selene_space_core.sina_symbols import sina_prefix as _core_sina_prefix
from selene_space_market.poll_backoff import PollBackoff
from selene_space_market.source import ExchangeSourceConfig
from selene_space_market.sources.astock_intraday_source import (
    _parse_hq_line,
    _to_epoch_ms,
)
from selene_space_types.market import KlineEvent, OrderBookL2Snapshot, TickerEvent

logger = logging.getLogger(__name__)

_SINA_HQ_URL = "https://hq.sinajs.cn/list={codes}"
# Sina rejects requests without a finance.sina.com.cn referer (403 Forbidden).
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn",
}

# Degradation path when Sina is unreachable. 腾讯 answers the same universe
# (BSE included) and is stable, but its payload shares no field layout with
# Sina's, so it needs its own parser — see parse_tencent_response.
_TENCENT_HQ_URL = "https://qt.gtimg.cn/q={codes}"
_TENCENT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://gu.qq.com/",
}
# 腾讯 reports volume in 手 (lots of 100 shares) on most boards, but the STAR
# board already reports shares — see _tencent_volume_in_shares. Without this
# factor the fallback would silently under-report every volume by 100x.
_TENCENT_LOT = 100.0
# Probed 2026-10-15 with real universe codes: 800/request returned 800/800 — the
# "truncation" an earlier probe reported came from synthesized non-existent codes
# being omitted, not a size cap. Still halved here: this is a degraded path
# running alongside an already-failing primary, so it stays gentler on the
# endpoint than Sina's 800.
_TENCENT_BATCH_SIZE = 400
# We index up to [37] (turnover, in 万元); 腾讯 sends 87 fields for BSE and 88 for
# SH/SZ, so 38 is a floor that still rejects a truncated or garbage payload.
_TENCENT_MIN_FIELDS = 38
# 腾讯 reports turnover in 万元 while Sina reports 元. Verified 2026-10-15 against
# the raw price/volume/amount triplet in field [35].
_TENCENT_AMOUNT_UNIT = 1.0e4
# 腾讯 volume/turnover live at these indices; the payload has no header row.
_TENCENT_FIELD_NAME = 1
_TENCENT_FIELD_LAST = 3
_TENCENT_FIELD_PREV_CLOSE = 4
_TENCENT_FIELD_OPEN = 5
_TENCENT_FIELD_VOLUME = 6
_TENCENT_FIELD_TIMESTAMP = 30
_TENCENT_FIELD_HIGH = 33
_TENCENT_FIELD_LOW = 34
_TENCENT_FIELD_AMOUNT = 37

# China has no DST, so a fixed +08:00 offset is exact — same convention as the
# intraday source and the ladder cross-section.
_CST = timezone(timedelta(hours=8))

# A whole-market cycle is heavy (≈5500 codes), so it polls far slower than the
# subscription source's 3s. In-session a cycle is paced by `_THROTTLE_SEC`
# between batches; off-hours it backs all the way off.
_POLL_INTERVAL_SEC = 8.0
_OFF_HOURS_INTERVAL_SEC = 300.0
_BACKOFF_CAP_SEC = 300.0
_STALE_AFTER_SEC = 120.0

# 800 codes/request was probed stable against the live endpoint (7 consecutive
# 800-code batches, 7/7 HTTP 200, ~7.7s wall for the whole market). The URL for
# 800 codes is ~7.5 KB, comfortably inside any proxy/edge limit.
_BATCH_SIZE = 800
# Serial + throttled: the endpoint tolerates a steady cadence but not bursts.
_THROTTLE_SEC = 0.15

# 09:15 covers the opening call auction; 15:05 covers the close print — same
# window as the intraday source.
_SESSION_START_MIN = 9 * 60 + 15
_SESSION_END_MIN = 15 * 60 + 5

# Sina field indices we read; the payload carries `_MIN_FIELDS` comma fields.
_MIN_FIELDS = 32

# Sina depth slots: buy1..5 are `(vol, price)` pairs from 10, sell1..5 from 20
# (see the intraday source's docstring for the full layout). Only level 1 is
# consumed — the sealed-at-limit check needs nothing deeper.
_SINA_BUY1_VOL = 10
_SINA_SELL1_VOL = 20

# 腾讯 depth slots are `price~vol` pairs (the transpose of Sina's `vol~price`):
# buy1..5 from 9, sell1..5 from 19.
_TENCENT_BUY1_VOL = 10
_TENCENT_SELL1_VOL = 20


def _in_session_window(now: datetime) -> bool:
    """True during A-share cash-session hours on a weekday.

    Holiday-unaware on purpose: it only throttles polling, and a closed session
    yields a frozen cross-section. Avoids pulling the exchange calendar into the
    market-data package.
    """
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return _SESSION_START_MIN <= minutes <= _SESSION_END_MIN


def _sina_prefix(code: str) -> str:
    """6-digit code → Sina exchange prefix (``sh`` / ``sz`` / ``bj``).

    Thin alias for :func:`selene_space_core.sina_symbols.sina_prefix`, kept so
    existing import sites stay valid. The rule used to be duplicated here and
    in ``astock_source._to_sina_symbol`` with *different board coverage* — the
    latter knew only SZ/SH and silently dropped every Beijing code. Both now
    resolve to one implementation.

    Deliberately narrower than ``market_rules.board_of`` (that stays the
    limit-price authority in ashare) — here it only picks a URL prefix. Raises
    ``ValueError`` for a non-6-digit code, so a bad universe entry fails at
    wiring time rather than polling the wrong venue (Sina answers
    ``var hq_str_<code>="";`` for a mismatched prefix).
    """
    # ``cast`` because the target package ships no ``py.typed``, so the call is
    # ``Any`` here — the same accommodation ``astock_source._to_sina_symbol`` makes.
    return cast("str", _core_sina_prefix(code))


def _code_to_symbol(code: str) -> str:
    """6-digit code → canonical ``<code>.<SH|SZ|BJ>`` symbol."""
    return cast("str", _core_code_to_symbol(code))


def _sina_best_levels(fields: list[str]) -> tuple[float | None, float | None]:
    """Sina fields → ``(bid1_vol, ask1_vol)`` in shares, or ``None`` per slot.

    Depth rides along free in the quote payload, so no extra request is needed.
    A slot is read leniently: an unparsable or absent quantity yields ``None``
    rather than raising, so one bad depth field cannot drop an otherwise-quotable
    row.

    ``None`` is deliberately distinct from ``0.0`` — ``0`` means a genuinely
    empty side (the sealed signal), while ``None`` means the number was not
    there. Collapsing them would report a missing field as a sealed board.
    """
    if len(fields) <= _SINA_SELL1_VOL:
        return None, None
    return (
        _optional_float(fields[_SINA_BUY1_VOL]),
        _optional_float(fields[_SINA_SELL1_VOL]),
    )


def _tencent_best_levels(
    fields: list[str], code: str
) -> tuple[float | None, float | None]:
    """腾讯 fields → ``(bid1_vol, ask1_vol)`` in **shares**, or ``None`` per slot.

    Same lenient contract as :func:`_sina_best_levels`, but 腾讯 quotes the level
    quantities in 手 (shares on the STAR board only) — the identical
    board-dependent unit :func:`_tencent_volume_in_shares` already normalises for
    ``volume``. Reporting the raw 手 here would make a 涨停's sealed 0 read the
    same but every non-zero depth 100x small, so the conversion is applied.
    """
    if len(fields) <= _TENCENT_SELL1_VOL:
        return None, None
    bid = _optional_float(fields[_TENCENT_BUY1_VOL])
    ask = _optional_float(fields[_TENCENT_SELL1_VOL])
    return (
        None if bid is None else _tencent_volume_in_shares(code, bid),
        None if ask is None else _tencent_volume_in_shares(code, ask),
    )


def _optional_float(value: str) -> float | None:
    """``float(value)`` or ``None`` — never raises on a missing/malformed slot."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SnapshotRow:
    """One symbol's whole-market snapshot row (normalised).

    Frozen so a consumer cannot mutate the shared cross-section in place — the
    map handed out by ``get_market_rows`` is the live poll cache, and an
    in-place edit would corrupt every later reader.

    ``prev_close`` is the exchange's official previous close (Sina field 2) —
    the same basis the limit-price rules use. ``last``/``high``/``low`` are raw,
    unadjusted prices, matching what the limit checks compare against.

    ``bid1_vol``/``ask1_vol`` are the best-level quantities in **shares** (the
    same unit as ``volume``), carrying the depth that rides along free in every
    payload. Only level 1 is kept: it is all the sealed-at-limit check needs
    (a truly sealed 涨停 has **no** resting sell order, so ``ask1_vol == 0``).
    ``None`` means the row carried no depth — deliberately distinct from a real
    ``0`` (no resting order at that price); conflating the two would report a
    missing field as a sealed board.
    """

    symbol: str
    name: str
    last: float
    prev_close: float
    open: float
    high: float
    low: float
    volume: float
    amount: float
    timestamp: int
    bid1_vol: float | None = None
    ask1_vol: float | None = None


def parse_snapshot_response(text: str) -> dict[str, SnapshotRow]:
    """Whole response body → ``{symbol: SnapshotRow}``.

    Symbols Sina answers with an empty payload (unknown code) or a not-yet-
    quotable row (pre-open / halted, ``last == 0``) are omitted rather than
    raising — the caller treats the returned count against the requested count
    to detect a degraded upstream. Malformed single rows are skipped;
    a wholesale format break shows up as an empty (or badly short) map.
    """
    rows: dict[str, SnapshotRow] = {}
    for line in text.splitlines():
        parsed = _parse_hq_line(line)
        if parsed is None:
            continue
        raw_code, fields = parsed
        if len(fields) < _MIN_FIELDS:
            continue
        try:
            last = float(fields[3])
        except ValueError:
            continue
        # Pre-open / halted: Sina publishes 0 in the price slot. Healthy
        # upstream, just not quotable yet — omit, do not fabricate a row.
        if last <= 0:
            continue
        try:
            prev_close = float(fields[2])
            bid1_vol, ask1_vol = _sina_best_levels(fields)
            row = SnapshotRow(
                symbol=f"{raw_code[2:]}.{raw_code[:2].upper()}",
                name=fields[0],
                last=last,
                prev_close=prev_close,
                open=float(fields[1]),
                high=float(fields[4]),
                low=float(fields[5]),
                volume=float(fields[8]),
                amount=float(fields[9]),
                timestamp=_to_epoch_ms(fields[30], fields[31]),
                bid1_vol=bid1_vol,
                ask1_vol=ask1_vol,
            )
        except ValueError:
            logger.debug(
                "AstockMarketSnapshotSource: malformed payload for %s", raw_code
            )
            continue
        rows[row.symbol] = row
    return rows


def _tencent_volume_in_shares(code: str, raw_volume: float) -> float:
    """腾讯 volume → shares, normalising the board-dependent unit.

    Measured live 2026-10-15 across 23 codes: the STAR board (``688``/``689``)
    reports volume in shares, every other board in 手. A flat ``* 100`` — which
    a first reading of the payload suggests — would inflate every STAR symbol by
    100x, and STAR is ~580 symbols of the universe. The board is recovered from
    the bare code because 腾讯's prefix (``sh``) does not distinguish main from
    STAR.
    """
    if code.startswith(("688", "689")):
        return raw_volume
    return raw_volume * _TENCENT_LOT


def _tencent_code_of(var_name: str) -> str:
    """``v_sh600519`` / ``v_sz000001`` / ``v_bj920819`` → bare 6-digit code."""
    return var_name.strip().rsplit("_", 1)[-1][2:]


def _tencent_timestamp_to_epoch_ms(raw: str) -> int:
    """``20261015143120`` (``%Y%m%d%H%M%S``, +08:00) → epoch milliseconds."""
    dt = datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=_CST)
    return int(dt.timestamp() * 1000)


def parse_tencent_response(text: str) -> dict[str, SnapshotRow]:
    """腾讯 ``qt.gtimg.cn`` body → ``{symbol: SnapshotRow}``.

    The fallback parser. 腾讯's payload shares nothing with Sina's beyond being
    a comma-ish list: it is ``~``-separated inside ``v_<prefix><code>="...";``
    assignments, its timestamp is a bare ``%Y%m%d%H%M%S`` integer rather than a
    date/time pair, and — the trap this function exists to defuse — **both its
    volume and turnover units differ from Sina's, and the volume unit is
    board-dependent**. See :func:`_tencent_volume_in_shares`.

    Halted rows are dropped on a different signal than Sina's. Sina zeroes the
    last price, so ``last <= 0`` identifies a halted name. 腾讯 instead echoes
    the *previous* close as the last price with the session's volume at zero
    (measured: a suspended BSE name returned ``last=8.17, volume=0, open=0``) —
    so filtering on ``last <= 0`` would emit a stale price as if it were today's
    close. Zero volume is the not-quotable signal here.
    """
    rows: dict[str, SnapshotRow] = {}
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        var_name, payload = chunk.split("=", 1)
        if not var_name.strip().startswith("v_"):
            continue
        fields = payload.strip().strip('"').split("~")
        if len(fields) < _TENCENT_MIN_FIELDS:
            continue
        code = _tencent_code_of(var_name)
        if not code.isdigit() or len(code) != 6:
            continue
        try:
            last = float(fields[_TENCENT_FIELD_LAST])
            raw_volume = float(fields[_TENCENT_FIELD_VOLUME])
            # Not-yet-quotable (halted / pre-open): 腾讯 leaves the last price at
            # the previous close but reports no volume. Emitting it would put a
            # stale price into the cross-section.
            if raw_volume <= 0 or last <= 0:
                continue
            bid1_vol, ask1_vol = _tencent_best_levels(fields, code)
            row = SnapshotRow(
                symbol=f"{code}.{_sina_prefix(code)[:2].upper()}",
                name=fields[_TENCENT_FIELD_NAME],
                last=last,
                prev_close=float(fields[_TENCENT_FIELD_PREV_CLOSE]),
                open=float(fields[_TENCENT_FIELD_OPEN]),
                high=float(fields[_TENCENT_FIELD_HIGH]),
                low=float(fields[_TENCENT_FIELD_LOW]),
                volume=_tencent_volume_in_shares(code, raw_volume),
                amount=float(fields[_TENCENT_FIELD_AMOUNT]) * _TENCENT_AMOUNT_UNIT,
                timestamp=_tencent_timestamp_to_epoch_ms(
                    fields[_TENCENT_FIELD_TIMESTAMP]
                ),
                bid1_vol=bid1_vol,
                ask1_vol=ask1_vol,
            )
        except ValueError:
            logger.debug(
                "AstockMarketSnapshotSource: malformed 腾讯 payload for %s", code
            )
            continue
        rows[row.symbol] = row
    return rows


class AstockMarketSnapshotSource:
    """ExchangeSource adapter for the whole-market A-share cross-section.

    Implements the :class:`~selene_space_market.source.ExchangeSource` surface
    so it registers without a cast, but only the pull-only cross-section is
    live: ``get_ticker``/``get_kline``/``get_orderbook`` are deliberate no-ops
    and ``on_ticker`` is a no-op sink — this source must never publish onto the
    shared bus (see module docstring).

    ``subscribe`` is used only to seed the universe (the poll loop iterates the
    subscribed set, exactly like the intraday source), not to express interest
    in a per-symbol stream.
    """

    def __init__(
        self,
        *,
        poll_interval_sec: float = _POLL_INTERVAL_SEC,
        off_hours_interval_sec: float = _OFF_HOURS_INTERVAL_SEC,
        batch_size: int = _BATCH_SIZE,
        throttle_sec: float = _THROTTLE_SEC,
    ) -> None:
        self._config = ExchangeSourceConfig(
            exchange="astock", market_type="market_snapshot"
        )
        self._running = False
        self._universe: set[str] = set()  # 6-digit codes to poll
        self._rows: dict[str, SnapshotRow] = {}
        self._poll_task: asyncio.Task[None] | None = None
        self._poll_interval = poll_interval_sec
        self._off_hours_interval = off_hours_interval_sec
        self._batch_size = max(1, batch_size)
        self._throttle = max(0.0, throttle_sec)
        self._backoff = PollBackoff(
            poll_interval_sec,
            cap_sec=_BACKOFF_CAP_SEC,
            stale_after_sec=_STALE_AFTER_SEC,
        )
        # Warn once per process when a whole cycle returns no parseable rows —
        # a payload-format break must be loud (the repository's existing clist
        # caller swallows exceptions and returns EMPTY, which is exactly the
        # silent-degradation failure this source is written to avoid).
        self._empty_warned = False
        # Warn once per outage episode when a cycle is served by the 腾讯
        # fallback instead of Sina — the switch is a standing condition.
        self._degraded_warned = False

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
        if self._universe and self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "AstockMarketSnapshotSource started (universe=%d, poll %ss in-session "
            "/ %ss off-hours, %d/batch)",
            len(self._universe),
            self._poll_interval,
            self._off_hours_interval,
            self._batch_size,
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
        # The universe is passed as either bare codes ("600519") or canonical
        # symbols ("600519.SH"); normalise to the 6-digit code the Sina URL
        # needs and validate eagerly so a bad entry fails at wiring time.
        code = symbol.strip().split(".")[0]
        _sina_prefix(code)  # raises ValueError on a non-A-share entry
        self._universe.add(code)
        if self._running and self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def unsubscribe(self, symbol: str, kline_interval: str | None = None) -> None:
        self._universe.discard(symbol.strip().split(".")[0])

    def set_universe(self, codes: Iterable[str]) -> None:
        """Replace the poll universe wholesale (bulk seed at wiring time).

        Like :meth:`subscribe`, this starts the poll loop if the source is
        already running — the composition layer seeds the whole A-share
        universe *after* ``MarketDataService.start()`` (the universe lives in
        the ashare package, which market-data must not import), so without the
        respawn here an already-started source would sit idle forever.
        """
        self._universe = {c.strip().split(".")[0] for c in codes}
        if self._running and self._universe and self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())

    # -- ExchangeSource: poll-style reads ----------------------------------

    def get_market_rows(self) -> dict[str, SnapshotRow]:
        """The current whole-market cross-section, keyed by canonical symbol.

        This is the only read face. The returned dict is the live cache —
        callers must treat it as read-only (rows are frozen, but the mapping is
        not copied to keep the hot path allocation-free).
        """
        return self._rows

    def get_ticker(self, symbol: str) -> TickerEvent | None:
        return None

    def get_kline(self, symbol: str) -> KlineEvent | None:
        return None

    def get_klines(self, symbol: str, limit: int = 100) -> list[KlineEvent]:
        return []

    def get_orderbook(self, symbol: str) -> OrderBookL2Snapshot | None:
        return None

    def get_symbols(self) -> list[str]:
        return [_code_to_symbol(c) for c in sorted(self._universe)]

    def staleness_seconds(self, code: str) -> int | None:
        return self._backoff.staleness_seconds(code)

    # -- ExchangeSource: push wiring (deliberately inert) ------------------

    def on_ticker(self, callback: Callable[[TickerEvent], Any]) -> None:
        # No-op sink: the service wires event_bus.publish here unconditionally,
        # but this source never emits — publishing a whole-market cross-section
        # would flood the bus the anomaly detector reads (see module docstring).
        return None

    def on_kline(self, callback: Callable[[KlineEvent], Any]) -> None:
        return None

    def on_orderbook(self, callback: Callable[[OrderBookL2Snapshot], Any]) -> None:
        return None

    # -- internals ----------------------------------------------------------

    async def fetch_market_rest(
        self, codes: list[str] | None = None
    ) -> dict[str, SnapshotRow]:
        """One-shot whole-market cycle, used for warmup and by tests."""
        universe = list(codes) if codes is not None else sorted(self._universe)
        return await asyncio.to_thread(self._fetch_cycle, universe)

    def _fetch_cycle(self, codes: list[str]) -> dict[str, SnapshotRow]:
        """Serial, throttled sweep over the whole universe → merged rows.

        A batch that raises is retried once; a batch that raises twice is
        dropped with a warning (its symbols simply stay absent this cycle) so
        one flaky request cannot abort the whole market. This is the opposite of
        a silent empty-return: every dropped batch is logged.

        When the *whole* Sina sweep yields nothing — the signature of the host
        being blocked or the endpoint moving — the cycle degrades to 腾讯 rather
        than reporting a quiet market. Partial Sina failure is not a degradation:
        the batches that answered are real, and the dropped ones are logged.
        """
        merged = self._sweep(
            codes, self._batch_size, self._fetch_batch, parse_snapshot_response
        )
        if merged or not codes:
            self._degraded_warned = False
            return merged
        if not self._degraded_warned:
            # Once per outage episode, not once per cycle: a degraded source is
            # a standing condition worth one loud line, not a per-8s metronome.
            self._degraded_warned = True
            logger.warning(
                "AstockMarketSnapshotSource: Sina returned no rows for %d code(s); "
                "degrading this cycle to 腾讯 qt.gtimg.cn",
                len(codes),
            )
        return self._sweep(
            codes,
            _TENCENT_BATCH_SIZE,
            self._fetch_tencent_batch,
            parse_tencent_response,
        )

    def _sweep(
        self,
        codes: list[str],
        batch_size: int,
        fetch_one: Callable[[list[str]], str | None],
        parse: Callable[[str], dict[str, SnapshotRow]],
    ) -> dict[str, SnapshotRow]:
        merged: dict[str, SnapshotRow] = {}
        batches = [codes[i : i + batch_size] for i in range(0, len(codes), batch_size)]
        for idx, batch in enumerate(batches):
            if idx:
                time.sleep(self._throttle)
            text = fetch_one(batch)
            if text is None:
                continue
            merged.update(parse(text))
        return merged

    def _fetch_batch(self, batch: list[str]) -> str | None:
        return self._http_batch(batch, _SINA_HQ_URL, _HEADERS, "Sina")

    def _fetch_tencent_batch(self, batch: list[str]) -> str | None:
        return self._http_batch(batch, _TENCENT_HQ_URL, _TENCENT_HEADERS, "腾讯")

    def _http_batch(
        self, batch: list[str], url_template: str, headers: dict[str, str], label: str
    ) -> str | None:
        codes = ",".join(f"{_sina_prefix(c)}{c}" for c in batch)
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                resp = requests.get(
                    url_template.format(codes=codes), headers=headers, timeout=20
                )
                resp.raise_for_status()
                # Both endpoints declare charset=GB18030; pin it rather than
                # trust detection (a mis-decode corrupts the name field).
                resp.encoding = "gb18030"
                # str() pins the type: requests is untyped here, so resp.text
                # is Any and mypy trips no-any-return on the -> str | None.
                return str(resp.text)
            except Exception as err:  # noqa: BLE001 - logged, not swallowed
                last_err = err
                if attempt == 0:
                    time.sleep(self._throttle)
        logger.warning(
            "AstockMarketSnapshotSource: %s batch of %d code(s) failed after retry: %s",
            label,
            len(batch),
            last_err,
        )
        return None

    def _interval_for_now(self) -> float:
        return (
            self._poll_interval
            if _in_session_window(datetime.now(_CST))
            else self._off_hours_interval
        )

    def _note_cycle(self, requested: int, rows: dict[str, SnapshotRow]) -> None:
        """Loud on a degenerate cycle, quiet on the normal quotable subset."""
        if rows:
            self._empty_warned = False
            logger.debug(
                "AstockMarketSnapshotSource: cycle produced %d/%d rows",
                len(rows),
                requested,
            )
            return
        if self._empty_warned or requested == 0:
            return
        self._empty_warned = True
        logger.warning(
            "AstockMarketSnapshotSource: a whole cycle returned no quotes for %d "
            "code(s) — the response format may have changed, or the endpoint is "
            "refusing this host",
            requested,
        )

    async def _poll_loop(self) -> None:
        try:
            while self._running:
                codes = sorted(self._universe)
                for code in codes:
                    self._backoff.record_attempt(code)
                try:
                    rows = await asyncio.to_thread(self._fetch_cycle, codes)
                except Exception:
                    # _fetch_cycle already guards per-batch; reaching here means
                    # something outside that guard broke. Back every code off and
                    # log — never silently idle.
                    for code in codes:
                        self._backoff.record_failure(code)
                    logger.warning(
                        "AstockMarketSnapshotSource poll cycle failed", exc_info=True
                    )
                    await asyncio.sleep(self._interval_for_now())
                    continue

                self._note_cycle(len(codes), rows)
                # Replace wholesale: a symbol absent this cycle (halted, or its
                # batch dropped) must not linger as a stale row in the
                # cross-section the ladder computes from.
                self._rows = rows
                for code in codes:
                    # Index backoff by code; rows are keyed by canonical symbol.
                    if _code_to_symbol(code) in rows:
                        self._backoff.record_success(code)
                    else:
                        self._backoff.record_failure(code)
                await asyncio.sleep(self._interval_for_now())
        finally:
            # Let start()/subscribe() respawn a crashed loop (they only create a
            # task when `_poll_task is None`).
            if self._poll_task is asyncio.current_task():
                self._poll_task = None

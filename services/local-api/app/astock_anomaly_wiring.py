"""Mount the A-share anomaly detector onto the local-api market service.

``MarketDataAstockContext`` adapts :class:`KlineDB` into the detector's
:class:`AstockContextProvider` protocol; ``wire_astock_anomaly_detector``
builds the detector with the ashare rule core (injected, not imported by
market-data) and subscribes it to the TickerEvent stream, republishing each
:class:`AnomalyEvent` back onto the bus for downstream consumers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pandas as pd

from selene_space_market.aggregators.astock_anomaly import (
    AstockAnomalyDetector,
    AstockContextProvider,
)
from selene_space_market.kline_db import KlineDB
from selene_space_market.service import MarketDataService
from selene_space_market.sources.astock_outage_tracker import OutageEpisode
from selene_space_types.market import KlineEvent, TickerEvent

if TYPE_CHECKING:
    from app.astock_anomaly_decision import AnomalyDecisionConsumer

logger = logging.getLogger(__name__)

# China has no DST, so a fixed +08:00 offset is exact — same convention as
# astock_intraday_source and the detector itself.
_CST_TZ = timezone(timedelta(hours=8))


class MarketDataAstockContext:
    """Daily-bar context read from the durable kline store.

    Bars strictly earlier than the current session day are the only authority
    for prev_close and the N-day baseline: intraday the daily partition may
    already hold a forming today-bar, which must never leak into the baseline.
    """

    def __init__(self, kline_db: KlineDB) -> None:
        self._kline_db = kline_db
        # Symbols already warned about the stale-import fallback, so the log
        # fires once per symbol per process instead of on every tick.
        self._stale_basis_warned: set[str] = set()

    def _warn_stale_basis_once(self, symbol: str) -> None:
        if symbol in self._stale_basis_warned:
            return
        self._stale_basis_warned.add(symbol)
        logger.warning(
            "A-share anomaly: no live daily bars for %s yet — falling back to the "
            "stale qfq import partition; level signals (N-day high/low, volume "
            "surge) are unreliable until the live sync lands",
            symbol,
        )

    def prev_close(self, symbol: str) -> float | None:
        bars = self._settled_bars(symbol, 1)
        return bars[-1].close if bars else None

    def name(self, symbol: str) -> str:
        # The venue name rides on the ticker itself (TickerEvent.name), so
        # the context has nothing to add here.
        return ""

    def prior_daily_bars(self, symbol: str, n: int) -> list[KlineEvent]:
        return self._settled_bars(symbol, n)

    # -- internals ---------------------------------------------------------

    def _settled_bars(self, symbol: str, n: int) -> list[KlineEvent]:
        today_start = datetime.now(_CST_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        # Two A-share daily partitions exist by convention and they disagree in
        # a way that matters: the bulk import files bars under
        # market_type="astock" and those are qfq (dividend-adjusted) and frozen
        # at the import date, while the live AstockSource syncs raw,
        # unadjusted bars under market_type="spot". The detector compares them
        # against the venue's raw intraday ticks, so it needs the raw basis —
        # prefer "spot". Only when the live partition is still empty (before
        # the first sync lands) does it fall back to the adjusted import, and
        # that fallback is announced rather than silent: judging today's price
        # against a stale, adjusted baseline fires plausible-looking but wrong
        # level signals, which is exactly the failure this ordering prevents.
        for market_type in ("spot", "astock"):
            frame = self._kline_db.get_klines(
                "astock",
                market_type,
                symbol,
                "1d",
                end_date=today_start,
            )
            if not frame.empty:
                if market_type == "astock":
                    self._warn_stale_basis_once(symbol)
                break
        if frame.empty:
            return []
        # get_klines' end_date is INCLUSIVE, and a daily bar is stamped at its
        # session date's midnight (astock_source._parse_rows). Today's still-
        # forming bar therefore lands exactly on the boundary and would be
        # returned as settled — making prev_close≈today's price, so limit-up /
        # limit-down comparisons could never trigger (the detector's core job
        # goes silently dead). Enforce "strictly earlier than the session day"
        # here rather than trusting the query bound.
        cutoff_ms = int(today_start.timestamp() * 1000)
        bars = [
            _row_to_kline(frame.iloc[i])
            for i in range(len(frame))
            if int(frame.iloc[i]["timestamp"]) < cutoff_ms
        ]
        return bars[-n:]


def _row_to_kline(row: pd.Series) -> KlineEvent:
    return KlineEvent(
        exchange=str(row["exchange"]),
        market_type=str(row["market_type"]),
        symbol=str(row["symbol"]),
        timestamp=int(row["timestamp"]),
        interval=str(row["interval"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        closed=bool(row["closed"]),
    )


def astock_session_open(now: datetime | None = None) -> bool:
    """True while the A-share continuous session is open (09:30-11:30 / 13:00-14:57).

    ``market_phase`` is the calendar-aware authority; AUCTION, the closing
    auction, lunch break and non-trading days all classify as something other
    than ``OPEN`` and are therefore excluded here. The calendar takes naive
    Beijing time, so a non-CST host is converted off a fixed +08:00 offset
    (China has no DST) rather than trusting local wall time.
    """
    from selene_space_ashare.trading_calendar import market_phase

    if now is None:
        now = datetime.now(_CST_TZ)
    # bool() pins the type: market_phase lives in a cross-package import mypy
    # cannot follow (so it resolves to Any), and warn_return_any forbids
    # leaking that out of a -> bool function.
    return bool(market_phase(now.replace(tzinfo=None)) == "OPEN")


def wire_astock_anomaly_detector(
    market_svc: MarketDataService,
    kline_db: KlineDB,
    *,
    is_session_open: Callable[[], bool],
    astock_symbols: list[str] | None = None,
) -> AstockAnomalyDetector:
    """Build the detector and subscribe it to the service's TickerEvent bus.

    ``astock_symbols`` is the set of A-share symbols the service can actually
    reach (used only for the startup log — an empty list is the visible
    failure signal, see plan R4). Returns the wired detector.
    """
    # Imported here so the wiring layer is the only place these meet.
    from selene_space_ashare.market_rules import limit_down_price, limit_up_price

    reachable = sorted(astock_symbols) if astock_symbols else []
    logger.info(
        "AstockAnomalyDetector wired: %d reachable A-share symbol(s): %s",
        len(reachable),
        reachable or "NONE — detector will starve (add astock symbols to favorites)",
    )

    context = MarketDataAstockContext(kline_db)
    detector = AstockAnomalyDetector(
        context=context,
        limit_up_price=limit_up_price,
        limit_down_price=limit_down_price,
        is_session_open=is_session_open,
    )

    def _handler(ticker: TickerEvent) -> None:
        # The bus is shared with every venue, and process() reads the daily
        # store before it can tell a crypto ticker has no A-share bars. Filter
        # at the edge so non-A-share tick rates never touch the event loop's
        # synchronous SQLite path.
        if ticker.exchange != "astock":
            return
        for event in detector.process(ticker):
            market_svc.event_bus.publish(event)

    market_svc.event_bus.subscribe(TickerEvent, _handler)
    return detector


def persist_intraday_gap(kline_db: KlineDB, episode: OutageEpisode) -> None:
    """Write one outage episode into the durable store's own gap table.

    Split from the source for testability: the poll loop calls this via
    ``AstockIntradaySource.set_gap_sink`` and the assertion that actually
    matters is that *this* store is the one the detector reads — so the
    binding is exercised by test, not by an inline closure in ``main.py``.
    """
    kline_db.record_l1_gap(
        source=episode.source,
        opened_at_ms=episode.opened_at_ms,
        closed_at_ms=episode.closed_at_ms,
        duration_sec=episode.duration_sec,
        symbols=sorted(episode.symbols_affected),
        volume_before=episode.volume_before,
        volume_after=episode.volume_after,
        volume_symbols=episode.volume_symbols,
    )


def wire_astock_intraday_gap_sink(
    market_svc: MarketDataService, kline_db: KlineDB
) -> bool:
    """Attach the outage ledger sink to the registered intraday source.

    Returns whether a sink was attached. A setter rather than a constructor
    argument because the source is registered before the kline store exists
    (``main.py`` builds sources, then ``KlineDataManager``); returns False when
    the intraday source is absent (``SELENE_ASTOCK_INTRADAY_SOURCE=0``), which
    is a supported crypto-only deployment, not an error.
    """
    source = market_svc.get_source("astock", "intraday")
    if source is None or not hasattr(source, "set_gap_sink"):
        return False
    source.set_gap_sink(lambda episode: persist_intraday_gap(kline_db, episode))
    return True


def wire_astock_anomaly_consumer(
    market_svc: MarketDataService,
    ledger: Any,
    *,
    capital_base: float,
    quantity_pct: float,
    mode: str | None = None,
) -> AnomalyDecisionConsumer:
    """Build the decision consumer and subscribe it to AnomalyEvent.

    ``ledger`` is the production ``HashChainLedger`` (``live_decisions.jsonl``)
    — the same file the live decision pipeline writes, so the anomaly decision
    stream shares its hash chain instead of forking a second ledger.

    ``mode`` is the already-resolved ``SELENE_ANOMALY_MODE`` value; ``None``
    means unset (observe). ``observe`` records only; ``suggest`` additionally
    parks a pending proposal for a human verdict (which records a confirmation,
    never an order). An illegal value raises ``ValueError`` here — the
    caller resolves it *outside* any try/except so a bad mode fails startup
    rather than degrading to a warning.
    """
    from app.astock_anomaly_decision import AnomalyDecisionConsumer
    from selene_space_types.market import AnomalyEvent

    consumer = AnomalyDecisionConsumer(
        ledger,
        capital_base=capital_base,
        quantity_pct=quantity_pct,
        mode=mode,
    )
    # Sync handler on purpose: the source publishes inside the event loop's
    # task (astock_intraday_source), so an async handler would be fire-and-
    # forget with its exceptions invisible.
    market_svc.event_bus.subscribe(AnomalyEvent, consumer.handle)
    return consumer


__all__ = [
    "AstockContextProvider",
    "MarketDataAstockContext",
    "astock_session_open",
    "persist_intraday_gap",
    "wire_astock_anomaly_consumer",
    "wire_astock_anomaly_detector",
    "wire_astock_intraday_gap_sink",
]

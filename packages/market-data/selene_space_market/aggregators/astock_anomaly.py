"""A-share intraday anomaly detection over the TickerEvent stream.

Subscribes (via the caller) to the L1 ticker flow and flags the price/volume
signal family: limit-up/down touch and break (炸板/翘板), N-day high/low, and
volume surge against the settled daily baseline read from
:class:`AstockContextProvider`.

The limit-price rule core and the session gate are dependency-injected so
this module stays free of the ashare/risk-engine layers (market-data is a
bottom-tier package). All per-symbol state is updated immutably: a frozen
state snapshot is replaced, never mutated in place.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol

from selene_space_types.market import AnomalyEvent, KlineEvent, TickerEvent

logger = logging.getLogger(__name__)

_HISTORY_CAP = 500
# Same tolerance semantics as ashare market_rules: a price counts as at-limit
# when it is within 1e-9 of the computed limit price.
_TOLERANCE = Decimal("1e-9")
# China has no DST; the fixed +08:00 offset mirrors astock_intraday_source.
_CST = timezone(timedelta(hours=8))
_SESSION_OPEN_MIN = 9 * 60 + 30
_SESSION_MINUTES = 15 * 60 - _SESSION_OPEN_MIN

Kind = str


class AstockContextProvider(Protocol):
    """Settled daily-bar context the detector reads per symbol."""

    def prev_close(self, symbol: str) -> float | None: ...
    def name(self, symbol: str) -> str: ...
    def prior_daily_bars(self, symbol: str, n: int) -> list[KlineEvent]: ...


@dataclass(frozen=True)
class _SymbolState:
    """Immutable per-symbol snapshot; replaced (never mutated) on change."""

    session_date: str
    at_limit_up: bool = False
    at_limit_down: bool = False
    # Level-triggered kinds already fired this session (fire once per session).
    emitted: frozenset[Kind] = frozenset()


class AstockAnomalyDetector:
    """Flags A-share anomalies on a stream of L1 ticker snapshots."""

    def __init__(
        self,
        *,
        context: AstockContextProvider,
        limit_up_price: Callable[[float, str, str], Decimal],
        limit_down_price: Callable[[float, str, str], Decimal],
        is_session_open: Callable[[], bool] | None = None,
        n_days: int = 60,
        volume_surge_ratio: float = 3.0,
        min_baseline_days: int = 20,
    ) -> None:
        self._context = context
        self._limit_up_price = limit_up_price
        self._limit_down_price = limit_down_price
        self._is_session_open = is_session_open
        self._n_days = n_days
        self._volume_surge_ratio = volume_surge_ratio
        self._min_baseline_days = min_baseline_days
        self._listeners: list[Callable[[AnomalyEvent], object]] = []
        self._history: list[AnomalyEvent] = []
        self._state: dict[str, _SymbolState] = {}
        self._gate_logged_session: str | None = None

    def on_anomaly(self, callback: Callable[[AnomalyEvent], object]) -> None:
        """Register a listener; exceptions inside it are contained."""
        self._listeners.append(callback)

    def process(self, ticker: TickerEvent) -> list[AnomalyEvent]:
        """Run one ticker snapshot through the state machine."""
        if self._is_session_open is not None and not self._is_session_open():
            self._note_gated(ticker)
            return []
        # The venue's own previous close (Sina field 2) is raw, current, and the
        # exact basis the exchange computes limit prices from — prefer it. The
        # daily-bar context is only a fallback: for A-shares its store is a stale
        # qfq import, so trusting it first would derive limits off an adjusted,
        # out-of-date close and fire plausible-looking but wrong signals.
        prev_close = ticker.prev_close
        if prev_close is None or prev_close <= 0:
            prev_close = self._context.prev_close(ticker.symbol)
        if prev_close is None or prev_close <= 0:
            return []

        session_date = _session_date_of(ticker.timestamp)
        state = self._state.get(ticker.symbol)
        if state is None or state.session_date != session_date:
            # Day boundary: prior at-limit flags and once-per-session set die.
            state = _SymbolState(session_date=session_date)

        price = Decimal(str(ticker.last_price))
        up = self._limit_up_price(prev_close, ticker.symbol, ticker.name)
        down = self._limit_down_price(prev_close, ticker.symbol, ticker.name)

        events = self._edge_events(ticker, state, price, up, down, prev_close)
        events += self._level_events(ticker, state, price, prev_close)

        if events:
            state = replace(
                state,
                at_limit_up=price >= up - _TOLERANCE,
                at_limit_down=price <= down + _TOLERANCE,
                emitted=state.emitted | frozenset(event.kind for event in events),
            )
        self._state[ticker.symbol] = state

        for event in events:
            self._remember(event)
            self._fanout(event)
        return events

    def get_recent(self, limit: int = 50) -> list[AnomalyEvent]:
        """Most recent anomalies, oldest first."""
        return (
            self._history[-limit:]
            if limit < len(self._history)
            else list(self._history)
        )

    # -- internals -------------------------------------------------------

    def _edge_events(
        self,
        ticker: TickerEvent,
        state: _SymbolState,
        price: Decimal,
        up: Decimal,
        down: Decimal,
        prev_close: float,
    ) -> list[AnomalyEvent]:
        """Limit touch/break transitions fire on every state flip."""
        at_up = price >= up - _TOLERANCE
        at_down = price <= down + _TOLERANCE
        events: list[AnomalyEvent] = []
        if at_up and not state.at_limit_up:
            events.append(self._event(ticker, "limit_up", prev_close))
        elif not at_up and state.at_limit_up:
            events.append(self._event(ticker, "broken_limit_up", prev_close))
        if at_down and not state.at_limit_down:
            events.append(self._event(ticker, "limit_down", prev_close))
        elif not at_down and state.at_limit_down:
            events.append(self._event(ticker, "limit_down_recovery", prev_close))
        return events

    def _level_events(
        self,
        ticker: TickerEvent,
        state: _SymbolState,
        price: Decimal,
        prev_close: float,
    ) -> list[AnomalyEvent]:
        """N-day high/low and volume surge fire at most once per session."""
        events: list[AnomalyEvent] = []
        bars = self._context.prior_daily_bars(ticker.symbol, self._n_days)
        # A "20-day high" over two bars (or a mean volume over two sessions) is
        # not a signal — it is the absence of a baseline. Until enough settled
        # history exists these three kinds stay silent rather than firing on
        # noise; edge kinds (limit touch/break) don't depend on history and are
        # unaffected.
        if len(bars) >= self._min_baseline_days:
            high = max(Decimal(str(bar.high)) for bar in bars)
            low = min(Decimal(str(bar.low)) for bar in bars)
            if "n_day_high" not in state.emitted and price > high:
                events.append(self._event(ticker, "n_day_high", prev_close))
            elif "n_day_low" not in state.emitted and price < low:
                events.append(self._event(ticker, "n_day_low", prev_close))
            if "volume_surge" not in state.emitted:
                fraction = _elapsed_fraction(ticker.timestamp)
                mean_volume = sum(bar.volume for bar in bars) / len(bars)
                # fraction == 0 (before/at 09:30) has a zero threshold, which
                # would fire on any positive volume — skip until the session
                # has actually elapsed.
                if fraction > 0 and mean_volume > 0:
                    threshold = self._volume_surge_ratio * mean_volume * fraction
                    if ticker.volume_24h > threshold:
                        events.append(self._event(ticker, "volume_surge", prev_close))
        return events

    def _event(
        self, ticker: TickerEvent, kind: Kind, prev_close: float
    ) -> AnomalyEvent:
        return AnomalyEvent(
            exchange=ticker.exchange,
            symbol=ticker.symbol,
            timestamp=ticker.timestamp,
            kind=kind,  # type: ignore[arg-type]
            price=ticker.last_price,
            prev_close=prev_close,
            # Ticker name first: Sina field 0 carries the venue display name
            # ("ST"/"退" markers included). Fall back to the context for
            # providers whose tickers carry no name (symbol→name tables).
            name=ticker.name or self._context.name(ticker.symbol),
        )

    def _note_gated(self, ticker: TickerEvent) -> None:
        # The gate rejects every tick for as long as it stays closed; log once
        # per session date so a closed market does not spam the log.
        session_date = _session_date_of(ticker.timestamp)
        if session_date != self._gate_logged_session:
            self._gate_logged_session = session_date
            logger.debug(
                "AstockAnomalyDetector: session closed, ticks suppressed (%s)",
                session_date,
            )

    def _remember(self, event: AnomalyEvent) -> None:
        self._history.append(event)
        if len(self._history) > _HISTORY_CAP:
            del self._history[: len(self._history) - _HISTORY_CAP]

    def _fanout(self, event: AnomalyEvent) -> None:
        for callback in self._listeners:
            try:
                callback(event)
            except Exception:
                logger.exception("astock-anomaly listener raised")


def _session_date_of(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=_CST).strftime("%Y-%m-%d")


def _elapsed_fraction(timestamp_ms: int) -> float:
    """Fraction of the 09:30–15:00 cash session elapsed at the ticker time.

    The lunch break is not subtracted: the denominator is the full 4h span,
    which makes the threshold conservative (higher) through the afternoon.
    """
    moment = datetime.fromtimestamp(timestamp_ms / 1000, tz=_CST)
    minutes = moment.hour * 60 + moment.minute
    elapsed = min(max(minutes - _SESSION_OPEN_MIN, 0), _SESSION_MINUTES)
    return elapsed / _SESSION_MINUTES

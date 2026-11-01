"""Large-trade rules: the event-driven half of the alert evaluator.

``price`` and ``indicator`` are states you can sample on a timer. A large trade
is not: :class:`LargeTradeDetector` promotes a print once
(``large_trade.py:47``), publishes it, and moves on. Polling ``get_recent()``
would miss everything that arrived between two rounds, so this rule type
subscribes to the bus instead.

**Threading.** The bus calls handlers synchronously, on the trade-ingestion
path. A SQLite read or a network send there would stall ingestion for every
subscriber behind it, so :meth:`LargeTradeAlertBridge.on_event` only hands the
event to a bounded queue — thread-safely, because a source may publish from a
thread of its own — and a consumer task does the reads, the comparison and the
notification.

**Bounded queue.** If the consumer falls behind, the incoming event is dropped
and counted rather than the queue growing without bound. Losing one print of a
burst is nearly invisible: the per-instrument quiet window below collapses
bursts anyway. Memory that grows with market activity is not recoverable.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from app.core.alert_evaluator import Hit, evaluate_large_trade
from app.core.alert_loop import AlertDeduper

logger = logging.getLogger(__name__)

#: Events buffered between the ingestion path and the consumer. Delivery is
#: bounded by network latency (``dispatch`` blocks, run off-thread); trades are
#: not. This is the cushion for that mismatch.
DEFAULT_QUEUE_DEPTH = 1000

#: How often the enabled-rule list is re-read while events flow. Every event
#: reads rules; a burst of hundreds per second must not mean hundreds of SQLite
#: reads. A newly created rule therefore arms within this window.
DEFAULT_RULE_TTL_SECONDS = 2.0

#: Drop logs are thinned: under a sustained backlog every dropped event would
#: otherwise log, and the bus runs per trade.
_DROP_LOG_EVERY = 100


class LargeTradeAlertBridge:
    """Bus subscriber + queue consumer for ``large_trade`` rules."""

    def __init__(
        self,
        *,
        load_rules: Callable[[], list[dict[str, Any]]],
        notify: Callable[[str], Any],
        loop: asyncio.AbstractEventLoop,
        queue_depth: int = DEFAULT_QUEUE_DEPTH,
        rule_ttl_seconds: float = DEFAULT_RULE_TTL_SECONDS,
        deduper: AlertDeduper | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._load_rules = load_rules
        self._notify = notify
        self._loop = loop
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_depth)
        self._rule_ttl = rule_ttl_seconds
        self._clock = clock
        self._rules: list[dict[str, Any]] | None = None
        self._rules_at = 0.0
        # The deduper is keyed on whatever string it is handed. This bridge
        # passes ``rule_id|symbol`` so that one rule can still page on ETH after
        # it paged on BTC, instead of the second instrument being muted for the
        # whole window.
        self._deduper = deduper or AlertDeduper()
        self._dropped = 0

    # -- bus side ----------------------------------------------------------

    def on_event(self, event: Any) -> None:
        """Bus handler. Must not block: it runs on the ingestion thread."""
        try:
            self._loop.call_soon_threadsafe(self._enqueue, event)
        except RuntimeError:
            # Loop already closed (shutdown). Count instead of raising into the
            # bus, whose failure log would then blame this handler per event.
            self._dropped += 1

    def _enqueue(self, event: Any) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped % _DROP_LOG_EVERY == 1:
                logger.warning(
                    "large-trade alert queue full; %d event(s) dropped so far",
                    self._dropped,
                )

    # -- consumer side -----------------------------------------------------

    async def run(self) -> None:
        """Consume forever; a return is a health failure (see task_liveness).

        One event's failure must not end the consumer — the next whale print is
        still somebody's alert.
        """
        while True:
            event = await self._queue.get()
            try:
                await self.handle_event(event)
            except Exception:  # noqa: BLE001
                logger.warning("large-trade alert handling failed", exc_info=True)

    async def handle_event(self, event: Any) -> list[Hit]:
        """Evaluate one event against the enabled rules; return delivered hits."""
        notional = _as_float(getattr(event, "notional_usd", None))
        symbol = str(getattr(event, "symbol", "") or "")
        if notional is None or notional <= 0:
            return []

        delivered: list[Hit] = []
        for rule in self._rules_for_now():
            rule_id = str(rule.get("id") or "")
            try:
                hit = evaluate_large_trade(rule, notional, symbol)
            except Exception:  # noqa: BLE001  one bad rule must not hide the rest
                logger.warning(
                    "large-trade rule %s evaluation failed",
                    rule_id or "<no-id>",
                    exc_info=True,
                )
                continue
            if hit is None:
                continue
            if not self._deduper.should_fire(f"{rule_id}|{symbol}"):
                logger.debug("large-trade rule %s suppressed for %s", rule_id, symbol)
                continue
            try:
                # dispatch() does blocking network I/O; this is the event loop.
                await asyncio.to_thread(self._notify, hit.text)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "large-trade rule %s notify failed", rule_id, exc_info=True
                )
                continue
            delivered.append(hit)
        return delivered

    def _rules_for_now(self) -> list[dict[str, Any]]:
        """Enabled ``large_trade`` rules, re-read at most once per TTL.

        A failed read keeps the previous snapshot: rules that were valid a
        second ago still describe what the user asked to be told about. The
        timestamp advances before the read, so a persistently broken store logs
        once per TTL instead of once per trade.
        """
        now = self._clock()
        if self._rules is not None and now - self._rules_at < self._rule_ttl:
            return self._rules
        self._rules_at = now
        try:
            rules = self._load_rules()
        except Exception:  # noqa: BLE001
            logger.warning(
                "large-trade rule read failed; using previous snapshot", exc_info=True
            )
            return self._rules or []
        self._rules = [r for r in rules if str(r.get("type") or "") == "large_trade"]
        return self._rules


def _as_float(value: Any) -> float | None:
    """Numeric notional, or ``None`` when the field is missing or not a number.

    ``LargeTradeEvent.notional_usd`` is a positive ``float``, so this only
    guards duck-typed events and a future shape change; ``bool`` is rejected
    because ``True`` is an ``int`` in Python.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)

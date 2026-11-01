"""The 30s alert loop: read rules, get numbers, notify on hits.

Three responsibilities, each of which is its own failure mode if got wrong:

1. **Deduplication.** A rule that is still true next round must not notify
   again — a price that stays above its threshold would otherwise notify every
   30 seconds forever. A per-rule quiet window (default 30 min) collapses those
   repeats. The window is keyed on the rule id alone, not on the observed value:
   a threshold that gets crossed in both directions should re-arm only by
   leaving and re-entering after the window, never by value jitter.
2. **Isolation.** One malformed rule or one dead feed must not end the round.
   Every rule is evaluated in its own guard; every notify is off-thread because
   ``dispatch`` does blocking network I/O and this loop shares the app's event
   loop with everything else.
3. **Honest silence.** ``evaluate`` returns ``None`` for both "not met" and "no
   data"; nothing is notified either way, and the distinction is logged.

Liveness: the loop is registered in ``task_liveness`` as critical. If it
returns or raises, ``/health/ready`` reports 503 rather than the process looking
healthy while silently no longer monitoring anything.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from app.core.alert_evaluator import AlertContext, Hit, evaluate_detail

logger = logging.getLogger(__name__)

#: How long a rule stays quiet after firing. Long enough that a persistent
#: condition does not page the user every round, short enough that a genuine
#: re-crossing later in the session is still reported.
DEFAULT_QUIET_WINDOW_SECONDS = 30 * 60

#: Round cadence. Matches the PA price-alert loop's 30s, for the same reason:
#: the price sources are poll caches, so faster rounds read the same numbers.
DEFAULT_INTERVAL_SECONDS = 30.0


class AlertDeduper:
    """Per-rule quiet windows; injectable clock so tests never sleep."""

    def __init__(
        self,
        window_seconds: float = DEFAULT_QUIET_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window_seconds
        self._clock = clock
        self._last_fired: dict[str, float] = {}

    def should_fire(self, rule_id: str) -> bool:
        """Whether a hit on *rule_id* may be delivered now."""
        now = self._clock()
        last = self._last_fired.get(rule_id)
        if last is not None and now - last < self._window:
            return False
        self._last_fired[rule_id] = now
        return True

    def forget(self, rule_id: str) -> None:
        """Drop a rule's window (it was deleted or disabled)."""
        self._last_fired.pop(rule_id, None)

    def prune(self, live_ids: set[str]) -> None:
        """Drop windows for rules that no longer exist, so the map cannot grow
        with the history of deleted rules."""
        for rule_id in list(self._last_fired):
            if rule_id not in live_ids:
                del self._last_fired[rule_id]


async def run_alert_round(
    *,
    rules: list[dict[str, Any]],
    ctx: AlertContext,
    deduper: AlertDeduper,
    notify: Callable[[str], Any],
    prefetch_indicators: (
        Callable[[list[dict[str, Any]]], Awaitable[dict[tuple[str, str], float]]] | None
    ) = None,
    prefetch_news: (
        Callable[
            [list[dict[str, Any]]],
            Awaitable[dict[str, tuple[int, str | None] | None]],
        ]
        | None
    ) = None,
) -> list[Hit]:
    """One evaluation pass over every enabled rule.

    Returns the hits that were actually delivered. A hit suppressed by the
    dedupe window is not in the list — callers use the return value to tell
    "fired" from "would have fired again".
    """
    indicators: dict[tuple[str, str], float] = {}
    if prefetch_indicators is not None:
        try:
            indicators = await prefetch_indicators(rules)
        except Exception:  # noqa: BLE001  prefetch failure = no indicator data
            logger.warning("alert indicator prefetch failed", exc_info=True)
            indicators = {}

    # News matching is a blocking SQLite read, so it happens here, off the
    # evaluator; the round then only does dict lookups. ``None`` per rule means
    # the query failed (``no_data``), which must not read as "no match".
    news: dict[str, tuple[int, str | None] | None] = {}
    if any(r.get("type") == "voice" for r in rules):
        if prefetch_news is None:
            # No news reader wired in this deployment. Leave the map empty:
            # the evaluator's ctx.news returns None for any rule, so voice
            # rules stay silent rather than firing on a phantom count.
            logger.debug("voice rules present but no news prefetcher configured")
        else:
            try:
                news = await prefetch_news(rules)
            except Exception:  # noqa: BLE001
                logger.warning("alert news prefetch failed", exc_info=True)
                news = {}

    # Price reads are cache hits, but a rule list can repeat a symbol; memoise
    # per round so two rules on one instrument cost one read.
    price_cache: dict[str, float | None] = {}

    def _price(symbol: str) -> float | None:
        if symbol not in price_cache:
            price_cache[symbol] = ctx.price(symbol)
        return price_cache[symbol]

    def _indicator(symbol: str, metric: str) -> float | None:
        return indicators.get((symbol, metric))

    def _news(rule_id: str) -> tuple[int, str | None] | None:
        return news.get(rule_id)

    round_ctx = AlertContext(price=_price, indicator=_indicator, news=_news)

    delivered: list[Hit] = []
    for rule in rules:
        rule_id = str(rule.get("id") or "")
        try:
            hit, reason = evaluate_detail(rule, round_ctx)
        except Exception:  # noqa: BLE001  one bad rule must not end the round
            logger.warning(
                "alert rule %s evaluation failed", rule_id or "<no-id>", exc_info=True
            )
            continue
        if hit is None:
            # ``no_data`` is logged at DEBUG: it is expected while a symbol is
            # cold, and a per-round WARNING would be noise. Anything else is a
            # rule that is simply not met, which needs no log at all.
            if reason == "no_data":
                logger.debug("alert rule %s skipped: no data", rule_id or "<no-id>")
            continue
        if not deduper.should_fire(rule_id):
            logger.debug("alert rule %s suppressed by quiet window", rule_id)
            continue
        try:
            # Off-thread: dispatch does synchronous network I/O.
            await asyncio.to_thread(notify, hit.text)
        except Exception:  # noqa: BLE001  a failed delivery must not stop the loop
            logger.warning("alert rule %s notify failed", rule_id, exc_info=True)
        delivered.append(hit)

    deduper.prune({str(r.get("id") or "") for r in rules})
    return delivered


async def alert_loop(
    *,
    list_enabled: Callable[[], list[dict[str, Any]]],
    make_ctx: Callable[[], AlertContext],
    notify: Callable[[str], Any],
    prefetch_indicators: (
        Callable[[list[dict[str, Any]]], Awaitable[dict[tuple[str, str], float]]] | None
    ) = None,
    prefetch_news: (
        Callable[
            [list[dict[str, Any]]],
            Awaitable[dict[str, tuple[int, str | None] | None]],
        ]
        | None
    ) = None,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    deduper: AlertDeduper | None = None,
) -> None:
    """Poll enabled rules forever. Never returns; a return is a health failure.

    The first round runs after one interval, matching the PA loop: the app has
    just started and the poll sources have not filled their caches yet, so an
    immediate pass would read empty prices and log a round of ``no_data``.
    """
    deduper = deduper or AlertDeduper()
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            rules = list_enabled()
        except Exception:  # noqa: BLE001  a store hiccup is one lost round
            logger.warning("alert rule read failed; round skipped", exc_info=True)
            continue
        try:
            await run_alert_round(
                rules=rules,
                ctx=make_ctx(),
                deduper=deduper,
                notify=notify,
                prefetch_indicators=prefetch_indicators,
                prefetch_news=prefetch_news,
            )
        except Exception:  # noqa: BLE001
            logger.warning("alert round failed", exc_info=True)

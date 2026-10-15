"""Independent funding-reversal watchdog for funding-arb pairs.

The funding-arb strategy's own exit triggers live inside ``get_signal`` and
only fire while ``strategy_runner`` is alive and every kline is processed.
If the strategy loop stops (process down, deployment paused) and funding
reverses, nobody unwinds the pair — a funding-capture position runs naked
with reverse directional exposure. This is the risk blind spot gap #3.

This monitor runs independently of the strategy loop: it polls the current
funding rate, and when the rate drops below the exit threshold (funding turns
unfavorable for the open short-perp leg), it:
  1. activates the kill switch for that strategy (blocks re-entry), then
  2. invokes a flatten callback to unwind the pair.

Convention: a funding-arb pair is long-spot / short-perp. The short leg
receives positive funding and pays negative funding, so the dangerous
direction is funding turning negative (shorts pay) — i.e. funding dropping
below ``exit_threshold`` (default 0.0001, mirroring the strategy's default).

The monitor is deliberately thin and duck-typed so it is unit-testable with
fakes: ``fetch_rate`` (async callable → object with ``.funding_rate`` /
``.symbol``), ``kill_switch`` (optional, ``activate_scope``), and ``flatten``
(optional async callable). Real wiring lives in the app (funding fetcher
closure for ``fetch_rate``, KillSwitch + DualLegExecutor for the actions).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


def _resolve_kill_scope() -> Any:
    """Return the STRATEGY kill-switch scope (enum when risk-engine installed)."""
    try:
        from selene_space_risk.kill_switch import KillSwitchScope
        return KillSwitchScope.STRATEGY
    except ImportError:
        # Duck-typed fallback: str-enums compare equal to their string value.
        return "STRATEGY"


class FundingReversalMonitor:
    """Watches a funding-arb pair's funding rate and force-flats on reversal.

    Args:
        fetch_rate: Async callable returning the current funding-rate snapshot
            (any object with ``.funding_rate`` and ``.symbol``) or None.
        exit_threshold: Reversal fires when ``funding_rate < exit_threshold``
            (default 0.0001 — matches FundingArbStrategy's exit threshold).
        strategy_id: Strategy to kill on reversal (blocks re-entry). When None,
            the kill-switch activation is skipped.
        kill_switch: Optional KillSwitch-compatible guard (``activate_scope``).
        flatten: Optional async callable that unwinds the pair (typically
            wraps ``DualLegExecutor.flatten``). Invoked once on reversal.
        poll_interval_s: Seconds between funding-rate polls in ``run()``.
        reason_prefix: Prefix for the kill-switch activation reason.
    """

    def __init__(
        self,
        fetch_rate: Callable[[], Awaitable[Any]],
        *,
        exit_threshold: float = 0.0001,
        strategy_id: str | None = None,
        kill_switch: Any | None = None,
        flatten: Callable[[], Awaitable[Any]] | None = None,
        poll_interval_s: float = 60.0,
        reason_prefix: str = "funding-reversal",
    ) -> None:
        self._fetch_rate = fetch_rate
        self._exit_threshold = exit_threshold
        self._strategy_id = strategy_id
        self._kill_switch = kill_switch
        self._flatten = flatten
        self._poll_interval_s = poll_interval_s
        self._reason_prefix = reason_prefix
        self._triggered = False
        self._triggered_at: float | None = None
        self._last_funding_rate: float | None = None
        self._task: asyncio.Task[None] | None = None

    # -- state ----------------------------------------------------------

    @property
    def triggered(self) -> bool:
        """Whether the reversal has already fired (until reset())."""
        return self._triggered

    @property
    def last_funding_rate(self) -> float | None:
        return self._last_funding_rate

    # -- detection ------------------------------------------------------

    async def check(self, rate: Any) -> bool:
        """Evaluate one funding-rate snapshot.

        Returns True if the reversal fired on this snapshot (kill switch
        activated + flatten invoked). Idempotent: fires at most once until
        ``reset()`` is called.
        """
        funding = float(getattr(rate, "funding_rate", 0.0))
        self._last_funding_rate = funding
        if self._triggered or funding >= self._exit_threshold:
            return False

        self._triggered = True
        self._triggered_at = time.time()
        symbol = getattr(rate, "symbol", "?")
        logger.warning(
            "Funding reversal on %s: rate=%s < exit_threshold=%s — force flat",
            symbol, funding, self._exit_threshold,
        )
        await self._fire(symbol, funding)
        return True

    async def _fire(self, symbol: str, funding: float) -> None:
        # 1. Kill the strategy scope first so nothing re-opens mid-unwind.
        if self._kill_switch is not None and self._strategy_id:
            try:
                self._kill_switch.activate_scope(
                    _resolve_kill_scope(),
                    self._strategy_id,
                    reason=f"{self._reason_prefix}: {symbol} funding {funding:.6f} < {self._exit_threshold}",
                )
            except Exception:
                logger.warning(
                    "Failed to activate kill switch on funding reversal "
                    "(strategy=%s) — flattening anyway", self._strategy_id, exc_info=True,
                )
        # 2. Unwind the pair. Flatten is best-effort: a failure is logged, not
        #    re-raised — the kill switch still blocks re-entry.
        if self._flatten is not None:
            try:
                await self._flatten()
            except Exception:
                logger.error("Funding-reversal flatten failed", exc_info=True)

    # -- poll loop ------------------------------------------------------

    async def run(self) -> None:
        """Poll the funding rate until cancelled, checking each snapshot."""
        while True:
            try:
                rate = await self._fetch_rate()
                if rate is not None:
                    await self.check(rate)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Funding-reversal poll failed", exc_info=True)
            await asyncio.sleep(self._poll_interval_s)

    def start(self) -> None:
        """Start the background poll loop (idempotent)."""
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self.run())

    async def stop(self) -> None:
        """Cancel the background poll loop."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def reset(self) -> None:
        """Re-arm after the pair is confirmed flat / a new entry begins."""
        self._triggered = False
        self._triggered_at = None

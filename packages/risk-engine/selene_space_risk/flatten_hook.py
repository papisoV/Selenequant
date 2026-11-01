"""FlattenHook — concurrent registry of flatten callbacks for unwinding positions.

Risk triggers (pre-trade CRITICAL, ContinuousRiskMonitor cycle) fire registered
flatten callbacks so open positions can be unwound. Callbacks are scoped by
strategy_id: ``register(callback)`` (strategy_id=None) means global (fires for
any strategy); ``register(callback, strategy_id="funding_arb")`` fires only when
that strategy is targeted. ``fire(None)`` fires everything.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Flatten callback: an async (or sync) callable taking no positional args.
FlattenCallback = Callable[[], Any]


@dataclass(frozen=True)
class FlattenReport:
    """Outcome of one fire: how many callbacks ran and how many failed."""

    n_fired: int
    n_failed: int


class FlattenHook:
    """Concurrent registry of flatten callbacks with per-callback error isolation."""

    def __init__(self) -> None:
        self._callbacks: list[tuple[FlattenCallback, str | None]] = []
        self._pending_tasks: set[asyncio.Task] = set()

    def register(self, callback: FlattenCallback, *, strategy_id: str | None = None) -> None:
        """Register a flatten callback. strategy_id=None → global (fires for any strategy)."""
        self._callbacks.append((callback, strategy_id))

    def unregister(self, callback: FlattenCallback) -> None:
        """Remove a previously registered callback."""
        self._callbacks = [(cb, sid) for (cb, sid) in self._callbacks if cb is not callback]

    def _matches(self, cb_strategy_id: str | None, strategy_id: str | None) -> bool:
        """Scope match: fire(None) hits everything; a scoped callback only fires for its strategy."""
        if strategy_id is None:
            return True
        if cb_strategy_id is None:
            return True
        return cb_strategy_id == strategy_id

    async def fire(self, strategy_id: str | None = None, *, reason: str = "") -> FlattenReport:
        """Await all matching callbacks. One raising callback never aborts the rest."""
        n_fired = 0
        n_failed = 0
        for cb, cb_strategy_id in list(self._callbacks):
            if not self._matches(cb_strategy_id, strategy_id):
                continue
            n_fired += 1
            try:
                result = cb()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                n_failed += 1
                logger.exception("Flatten callback failed (reason=%s)", reason)
        return FlattenReport(n_fired=n_fired, n_failed=n_failed)

    def fire_and_forget(self, strategy_id: str | None = None, *, reason: str = "") -> None:
        """Schedule a fire without awaiting. Sync-safe: no running loop → pending log only.

        Keeps a strong ref to the task until it completes so Python GC cannot
        cancel it mid-flight.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.info(
                "FlattenHook: no running event loop — flatten pending (strategy_id=%s reason=%s)",
                strategy_id,
                reason,
            )
            return
        task = asyncio.create_task(self.fire(strategy_id, reason=reason))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

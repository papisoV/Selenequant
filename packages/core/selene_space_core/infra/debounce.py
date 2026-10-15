"""Quiet-window coalescing for async callbacks (trailing-edge debounce)."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable


class Debouncer:
    """Wrap an async-capable callback so it fires once, window_seconds after
    the most recent call(). Each call replaces pending arguments and restarts
    the countdown; cancel() drops any pending fire."""

    def __init__(self, fn: Callable[..., Any], window_seconds: float = 0.3) -> None:
        self._callback = fn
        self._quiet_period = window_seconds
        self._pending: asyncio.TimerHandle | None = None

    async def call(self, *args: Any, **kwargs: Any) -> None:
        loop = asyncio.get_running_loop()
        if self._pending is not None:
            self._pending.cancel()

        done = asyncio.Event()

        def _on_quiet() -> None:
            outcome = self._callback(*args, **kwargs)
            if inspect.isawaitable(outcome):

                async def _run() -> None:
                    await outcome
                    done.set()

                loop.create_task(_run())
            else:
                done.set()

        self._pending = loop.call_later(self._quiet_period, _on_quiet)
        # call() itself returns immediately; the fire happens on the timer.
        await asyncio.sleep(0)

    def cancel(self) -> None:
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None

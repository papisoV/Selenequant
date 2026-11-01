"""Idle watchdog: fires once per arm cycle when a feed goes quiet."""

from __future__ import annotations

from asyncio import CancelledError, Task, create_task, sleep
from collections.abc import Callable
from inspect import isawaitable
from logging import getLogger
from time import monotonic
from typing import Any, NamedTuple

_log = getLogger(__name__)

Callback = Callable[..., Any]


class StallTimeoutMeta(NamedTuple):
    idle_s: float
    timeout_s: float


class StallWatchdog:
    """Detect feed silence; sync or async callbacks are both accepted."""

    def __init__(
        self,
        *,
        on_timeout: Callback | None = None,
        timeout_s: float = 30.0,
        label: str = "ws-watchdog",
        **kwargs: Any,
    ) -> None:
        if not timeout_s > 0:
            raise ValueError("timeout_s must be positive")
        check_interval_s: float | None = kwargs.pop("check_interval_s", None)
        self._timeout_s = timeout_s
        self._check_interval_s = (
            check_interval_s if check_interval_s is not None else timeout_s / 4.0
        )
        self._on_timeout = on_timeout
        self._label = label
        self._task: Task[None] | None = None
        self._last_touch: float = 0.0
        self._armed = False
        self._stopped = False

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopped = False
            self._task = create_task(self._check_loop())

    def arm(self) -> None:
        self._last_touch = monotonic()
        self._armed = True

    def touch(self) -> None:
        self._last_touch = monotonic()

    def disarm(self) -> None:
        self._armed = False

    def stop(self) -> None:
        self._stopped = True
        self._armed = False
        task = self._task
        if task is not None and not task.done():
            task.cancel()

    @property
    def is_stopped(self) -> bool:
        return self._stopped

    @property
    def is_armed(self) -> bool:
        return bool(self._armed) and not self._stopped

    async def _check_loop(self) -> None:
        try:
            while True:
                if self._stopped:
                    break
                await sleep(self._check_interval_s)
                await self._check()
        except CancelledError:  # normal shutdown path
            return

    async def _check(self) -> None:
        if not self._armed:
            return
        now = monotonic()
        idle = now - self._last_touch
        if idle < self._timeout_s:
            return
        meta = StallTimeoutMeta(idle_s=idle, timeout_s=self._timeout_s)
        self._armed = False
        _log.warning(
            "%s fired after %.1fs idle (timeout %.1fs)",
            self._label,
            idle,
            self._timeout_s,
        )
        if self._on_timeout is None:
            return
        result = self._on_timeout(meta)
        if isawaitable(result):
            await result

"""Failure-isolation valve for async operations: closed / open / half-open."""

from __future__ import annotations

import enum
import time
from collections.abc import Awaitable, Callable
from typing import Any


class CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """The circuit rejected a call because it is open."""


class CircuitBreaker:
    """Track consecutive failures of call(fn).

    CLOSED: calls pass through; N failures in a row trip the breaker.
    OPEN:   every call raises CircuitOpenError without touching fn.
    HALF_OPEN (after recovery_timeout): the next outcome decides —
            success closes and clears the counter, failure re-trips.
    reset() returns to a clean CLOSED state at any time."""

    def __init__(
        self, failure_threshold: int = 5, recovery_timeout: float = 60.0
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown = recovery_timeout
        self._tripped_at: float | None = None
        self._consecutive_failures = 0
        self._phase = CircuitState.CLOSED

    @property
    def state(self) -> CircuitState:
        if (
            self._phase is CircuitState.OPEN
            and self._tripped_at is not None
            and time.monotonic() - self._tripped_at >= self._cooldown
        ):
            self._phase = CircuitState.HALF_OPEN
        return self._phase

    async def call(self, fn: Callable[[], Awaitable]) -> Any:
        if self.state is CircuitState.OPEN:
            raise CircuitOpenError(f"circuit open for {self._cooldown}s cooldown")
        try:
            outcome = await fn()
        except BaseException:
            self._on_failure()
            raise
        self._on_success()
        return outcome

    def reset(self) -> None:
        self._phase = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._tripped_at = None

    # -- transitions -----------------------------------------------------------

    def _on_failure(self) -> None:
        if self._phase is CircuitState.HALF_OPEN:
            self._trip()
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            self._trip()

    def _on_success(self) -> None:
        self._phase = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._tripped_at = None

    def _trip(self) -> None:
        self._phase = CircuitState.OPEN
        self._tripped_at = time.monotonic()

"""Sliding-window request throttling with server Retry-After support."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)


def _parse_retry_after(value: str | int | float) -> float | None:
    """Interpret a Retry-After header value: seconds or an HTTP-date."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    delay = when.timestamp() - time.time()
    return max(delay, 0.0)


class SlidingWindowRateLimiter:
    """Serialise ``acquire()`` calls against a per-window call budget.

    A deque of recent acquire timestamps defines the window; when the budget
    is exhausted the caller sleeps until the oldest entry slides out. A
    server-sent Retry-After overrides the window arithmetic entirely because
    the venue knows its own throttle state better than we do.
    """

    def __init__(self, *, calls_per_window: int = 1200, window_ms: int = 60000) -> None:
        if calls_per_window < 1:
            raise ValueError("calls_per_window must be >= 1")
        if window_ms < 1:
            raise ValueError("window_ms must be >= 1")
        self._calls_per_window = calls_per_window
        self._window_s = window_ms / 1000.0
        self._stamps: deque[float] = deque()
        self._retry_after_until: float | None = None

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            # Drop stamps that have left the sliding window.
            cutoff = now - self._window_s
            while self._stamps and self._stamps[0] <= cutoff:
                self._stamps.popleft()

            if self._retry_after_until is not None:
                if now < self._retry_after_until:
                    await asyncio.sleep(self._retry_after_until - now)
                    self._retry_after_until = None
                    continue
                self._retry_after_until = None

            if len(self._stamps) < self._calls_per_window:
                self._stamps.append(now)
                return

            wait = self._stamps[0] + self._window_s - now + 0.001
            await asyncio.sleep(max(wait, 0.001))

    def set_retry_after(self, value: str | int | float) -> None:
        delay = _parse_retry_after(value)
        if delay is not None and delay > 0:
            self._retry_after_until = time.monotonic() + delay

    def reset(self) -> None:
        self._stamps.clear()
        self._retry_after_until = None

    @property
    def remaining(self) -> int:
        now = time.monotonic()
        cutoff = now - self._window_s
        while self._stamps and self._stamps[0] <= cutoff:
            self._stamps.popleft()
        live = sum(1 for s in self._stamps if s > cutoff)
        return max(self._calls_per_window - live, 0)

    _parse_retry_after = staticmethod(_parse_retry_after)

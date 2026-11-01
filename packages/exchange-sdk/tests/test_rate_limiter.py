"""SlidingWindowRateLimiter: window saturation, expiry recovery, Retry-After."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from selene_space_exchange.base.rate_limiter import SlidingWindowRateLimiter


def _elapsed_seconds() -> float:
    return time.monotonic()


class TestWindowSaturation:
    @pytest.mark.asyncio
    async def test_burst_fits_then_next_call_waits(self) -> None:
        limiter = SlidingWindowRateLimiter(calls_per_window=3, window_ms=500)
        burst = [_limiter_acquire(limiter) for _ in range(3)]

        fast = await _timed(burst)
        assert fast < 0.1  # the initial allowance is free

        waited = await _timed([_limiter_acquire(limiter)])
        assert waited >= 0.4  # ~the 500ms window must slide first

    @pytest.mark.asyncio
    async def test_slots_return_once_window_passes(self) -> None:
        limiter = SlidingWindowRateLimiter(calls_per_window=2, window_ms=200)
        for _ in range(2):
            await limiter.acquire()

        await asyncio.sleep(0.25)  # let every stamp age out

        began = _elapsed_seconds()
        await limiter.acquire()
        assert _elapsed_seconds() - began < 0.1


async def _timed(calls):
    began = time.monotonic()
    for call in calls:
        await call
    return time.monotonic() - began


def _limiter_acquire(limiter):
    """Coroutine factory so timed bursts read left-to-right."""
    return limiter.acquire()


class TestRemainingCounter:
    @pytest.mark.asyncio
    async def test_counter_tracks_each_take(self) -> None:
        limiter = SlidingWindowRateLimiter(calls_per_window=5, window_ms=1000)
        assert limiter.remaining == 5
        for want in (4, 3):
            await limiter.acquire()
            assert limiter.remaining == want


class TestServerRetryAfter:
    WINDOW_MS = 60_000
    CAPACITY = 100

    def _limiter(self) -> SlidingWindowRateLimiter:
        return SlidingWindowRateLimiter(
            calls_per_window=self.CAPACITY, window_ms=self.WINDOW_MS
        )

    @pytest.mark.asyncio
    async def test_numeric_seconds_delay_honored(self) -> None:
        limiter = self._limiter()
        limiter.set_retry_after(0.2)
        began = time.monotonic()
        await limiter.acquire()
        assert time.monotonic() - began >= 0.15

    @pytest.mark.asyncio
    async def test_string_number_coerced(self) -> None:
        limiter = self._limiter()
        limiter.set_retry_after("0.2")
        began = time.monotonic()
        await limiter.acquire()
        assert time.monotonic() - began >= 0.15

    def test_http_date_parses_to_future_delay(self) -> None:
        """HTTP-date carries 1s resolution, so expect roughly the 2s offset."""
        limiter = self._limiter()
        soon = datetime.now(UTC) + timedelta(seconds=2)
        delay = limiter._parse_retry_after(format_datetime(soon, usegmt=True))
        assert delay is not None
        assert delay > 0.5

    @pytest.mark.asyncio
    async def test_server_directive_beats_open_window(self) -> None:
        """Plenty of slots remain, yet the server pause still applies."""
        limiter = self._limiter()
        limiter.set_retry_after(0.2)
        began = time.monotonic()
        await limiter.acquire()
        assert time.monotonic() - began >= 0.15


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_reset_refills_the_budget(self) -> None:
        limiter = SlidingWindowRateLimiter(calls_per_window=2, window_ms=10_000)
        for _ in range(2):
            await limiter.acquire()
        assert limiter.remaining == 0

        limiter.reset()
        assert limiter.remaining == 2

    @pytest.mark.parametrize("bad_kwargs", [{"calls_per_window": 0}, {"window_ms": 0}], ids=["zero-calls", "zero-window"])
    def test_nonpositive_config_rejected(self, bad_kwargs: dict) -> None:
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(**bad_kwargs)

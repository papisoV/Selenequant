"""Supervise a stream client: redial with backoff, resubscribe, rate-limit."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from collections import deque

from selene_space_core.resilience.backoff import BackoffPolicy
from selene_space_core.resilience.error_classifier import ErrorCategory, classify_error
from selene_space_exchange.base.ws_client import BaseWSClient
from selene_space_exchange.types import ReconnectConfig

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = ReconnectConfig()

#: Window for the restart circuit breaker.
_RESTART_WINDOW_S = 3600.0


class ReconnectManager:
    """Owns the connect/listen loop for one client until stopped.

    Failure taxonomy comes from ``classify_error``: AUTH-class failures stop
    the loop (credentials will not fix themselves), everything else backs off
    and retries. Restart attempts per rolling hour are capped to avoid hot
    spinning against a venue outage.
    """

    def __init__(
        self,
        client: BaseWSClient,
        config: ReconnectConfig | None = None,
        *,
        listen: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._client = client
        self._config = config or _DEFAULT_CONFIG
        cfg = self._config
        self._backoff = BackoffPolicy(
            initial=cfg.initial_delay_s,
            max_delay=cfg.max_delay_s,
            factor=cfg.factor,
            jitter=False,
        )
        self._listen = listen
        self._running = False
        self._attempt = 0
        self._restart_timestamps: deque[float] = deque()
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        self._running = True
        self._stopping.clear()
        try:
            while not self._stopping.is_set():
                try:
                    await self._client.connect()
                except Exception as exc:
                    category = classify_error(exc)
                    if category is ErrorCategory.AUTH:
                        logger.error("auth failure (%s) — not retrying", exc)
                        break
                    if not self._check_restart_rate():
                        break
                    delay = self._compute_delay()
                    self._attempt += 1
                    logger.warning(
                        "connect failed (%s/%s), retry in %.2fs",
                        type(exc).__name__,
                        category.value,
                        delay,
                    )
                    try:
                        await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                        break  # stop requested during backoff
                    except asyncio.TimeoutError:
                        pass
                    continue

                self._attempt = 0
                watcher = asyncio.create_task(self._client.wait_disconnected())
                try:
                    if self._listen is not None:
                        await self._listen()
                    else:
                        await watcher
                except Exception as exc:
                    logger.warning("listen loop raised: %r", exc)
                finally:
                    if not watcher.done():
                        watcher.cancel()
                if self._stopping.is_set():
                    break
                if not self._check_restart_rate():
                    break
                delay = self._compute_delay()
                self._attempt += 1
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                    break
                except asyncio.TimeoutError:
                    pass
                await self._resubscribe()
        finally:
            self._running = False

    async def stop(self) -> None:
        self._stopping.set()
        await self._client.close()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def attempt(self) -> int:
        return self._attempt

    def _compute_delay(self) -> float:
        base = self._backoff.calculate_delay(max(self._attempt - 1, 0))
        spread = self._config.jitter
        if spread <= 0:
            return min(base, self._config.max_delay_s)
        lo, hi = 1.0 - spread, 1.0 + spread
        return min(random.uniform(lo, hi) * base, self._config.max_delay_s)

    def _check_restart_rate(self) -> bool:
        now = time.monotonic()
        cutoff = now - _RESTART_WINDOW_S
        while self._restart_timestamps and self._restart_timestamps[0] < cutoff:
            self._restart_timestamps.popleft()
        if len(self._restart_timestamps) >= self._config.max_restarts_per_hour:
            logger.warning(
                "restart budget exhausted (%d/hour) — giving up",
                self._config.max_restarts_per_hour,
            )
            return False
        self._restart_timestamps.append(now)
        return True

    async def _resubscribe(self) -> None:
        streams = list(self._client.subscriptions)
        if streams:
            await self._client.subscribe(streams)

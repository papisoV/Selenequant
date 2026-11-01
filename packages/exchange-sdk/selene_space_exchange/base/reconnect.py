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
        #: Flips True after the first successful connect, so the loop can tell
        #: a cold dial from a re-dial and only restore streams on the latter.
        self._was_connected = False
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
                    if not await self._backoff_before_redial(
                        f"connect failed ({type(exc).__name__}/{category.value})"
                    ):
                        break
                    continue

                self._attempt = 0
                if self._was_connected:
                    # Re-dial after a drop: replay the subscription ledger on
                    # the socket we just opened. Must run *after* connect —
                    # sending on the previous socket would raise (2026-10
                    # Binance 1008 incident). A refusal means this connection
                    # carries no streams, so drop it and back off rather than
                    # listening to a streamless socket. Cold dials skip this:
                    # the client itself flushes its pre-connect buffer.
                    if not await self._resubscribe():
                        try:
                            await self._client.close()
                        except Exception as exc:
                            logger.debug("close after refused resubscribe: %r", exc)
                        if not await self._backoff_before_redial("resubscribe refused"):
                            break
                        continue
                self._was_connected = True

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
                if not await self._backoff_before_redial("stream dropped"):
                    break
        finally:
            self._running = False

    async def _backoff_before_redial(self, reason: str) -> bool:
        """Wait out the restart budget and backoff. False = stop the loop.

        The wait races ``_stopping`` so ``stop()`` interrupts a long backoff
        instead of holding the task hostage for the full delay.
        """
        if not self._check_restart_rate():
            return False
        delay = self._compute_delay()
        self._attempt += 1
        logger.warning("%s — redial in %.2fs", reason, delay)
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return True
        return False  # stop requested during backoff

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
        # float(): mypy cannot resolve the cross-package selene_space_core import
        # here, so calculate_delay() degrades to Any and warn_return_any trips.
        # Same explicit-cast idiom backoff.py itself uses.
        base = float(self._backoff.calculate_delay(max(self._attempt - 1, 0)))
        spread = self._config.jitter
        if spread <= 0:
            return min(base, self._config.max_delay_s)
        lo, hi = 1.0 - spread, 1.0 + spread
        return min(float(random.uniform(lo, hi)) * base, self._config.max_delay_s)

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

    async def _resubscribe(self) -> bool:
        """Push the subscription ledger onto the live socket. False = refused.

        Never raises: a venue that rejects the frame (rate limit, policy
        violation) must cost one reconnect cycle, not the supervisor.
        """
        streams = list(self._client.subscriptions)
        if not streams:
            return True
        try:
            await self._client.subscribe(streams)
        except Exception as exc:
            logger.warning("resubscribe refused (%s) — will redial", type(exc).__name__)
            return False
        return True

"""Persistent asyncio event loop manager for Telethon operations.

Runs a dedicated daemon thread with a persistent asyncio event loop.
Short operations use run_coroutine() (blocking).
Long operations (crawl) use get_loop().create_task() (non-blocking).
"""
import asyncio
import logging
import threading
from concurrent.futures import Future

logger = logging.getLogger(__name__)


class AsyncLoopManager:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()

    def start(self) -> None:
        if self._loop is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._started.wait(timeout=10)
        logger.info("AsyncLoopManager started")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._started.set()
        self._loop.run_forever()

    def get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self.start()
        assert self._loop is not None
        return self._loop

    def run_coroutine(self, coro, timeout: float = 30.0):
        """Run a coroutine on the shared loop, blocking until result.

        Use for short operations (dialogs, login, fetch).
        Returns the coroutine result or raises on timeout/exception.
        """
        loop = self.get_loop()
        future: Future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except Exception:
            future.cancel()
            raise

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._loop = None
            self._thread = None
            logger.info("AsyncLoopManager stopped")

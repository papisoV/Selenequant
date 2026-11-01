"""DB-backed logging handler for the system_logs panel (v008 table).

Python's ``logging.Handler.emit`` is synchronous, but the storage layer is
async (AsyncSQLiteManager) — so the handler only enqueues into a bounded
deque and a background asyncio task drains it into the DB in batches.

Fail-safe rules (the only place in this codebase where swallowing is correct):
  * ``emit`` must never raise — an exception inside the logging pipeline
    can break every logger in the process;
  * ``drain`` must never raise — a DB write failure logged at ERROR level
    would re-enter this same handler and loop.
Recursion guard: this handler ignores records from its own module.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from typing import Any, Callable

logger = logging.getLogger(__name__)


class DBLogHandler(logging.Handler):
    """Enqueue INFO+ records; a drain task writes them to system_logs."""

    def __init__(self, level: int = logging.INFO, capacity: int = 200) -> None:
        super().__init__(level=level)
        self._queue: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._exc_formatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name == __name__:
                return  # recursion guard: never log about our own writes
            if not record.levelno >= self.level:
                return  # direct emit() bypasses Handler.handle()'s level gate
            self._queue.append(
                {
                    "id": f"log-{uuid.uuid4().hex[:16]}",
                    "timestamp": int(record.created),
                    "level": record.levelname.lower(),
                    "logger": record.name,
                    "message": self.format(record),
                    "module": record.module,
                    "line": record.lineno,
                    "exc": (
                        None
                        if record.exc_info is None
                        else self._exc_formatter.formatException(record.exc_info)
                    ),
                }
            )
        except Exception:
            self.handleError(record)

    async def drain(self, db: Any, batch_size: int = 50) -> int:
        """Pop up to batch_size records and INSERT them. Returns rows written.

        Never raises: a failed write drops the popped batch so one bad
        batch cannot wedge the drain loop.
        """
        batch: list[tuple] = []
        try:
            if db is None or not self._queue:
                return 0
            while self._queue and len(batch) < batch_size:
                r = self._queue.popleft()
                batch.append(
                    (
                        r["id"],
                        r["timestamp"],
                        r["level"],
                        r["logger"],
                        r["message"],
                        r["module"],
                        r["line"],
                        r["exc"],
                    )
                )
            if batch:
                await db.execute_many(
                    "INSERT INTO system_logs "
                    "(id, timestamp, level, logger, message, module, line, exc) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    batch,
                )
            return len(batch)
        except Exception:
            logger.debug("drain failed; dropped batch of %d", len(batch))
            return 0

    @property
    def pending(self) -> int:
        return len(self._queue)


async def _drain_loop(
    handler: DBLogHandler, db_provider: Callable[[], Any], interval: float = 2.0
) -> None:
    """Periodically drain queued log records into the DB. Never exits on error."""
    while True:
        await asyncio.sleep(interval)
        try:
            await handler.drain(db_provider())
        except Exception:
            pass


class DBLogManager:
    """Owns the handler + drain task lifecycle for lifespan wiring."""

    def __init__(self, db_provider: Callable[[], Any]) -> None:
        self.handler = DBLogHandler()
        self._db_provider = db_provider
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                _drain_loop(self.handler, self._db_provider)
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            # gather(return_exceptions=True) swallows the cancellation we just
            # requested — CancelledError is a BaseException, so `except
            # Exception` never saw it and it escaped into lifespan shutdown.
            # If *we* are being cancelled, gather still raises, as it should.
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        # final flush (best-effort)
        try:
            await self.handler.drain(self._db_provider())
        except Exception:
            pass


def attach_db_log_handler(db_provider: Callable[[], Any]) -> DBLogManager:
    """Create the manager, attach its handler to the root logger, start drain."""
    manager = DBLogManager(db_provider)
    logging.getLogger().addHandler(manager.handler)
    manager.start()
    return manager

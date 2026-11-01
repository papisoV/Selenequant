"""Signal service - runs batch factor processing in a background thread."""
from __future__ import annotations

import logging
import threading
from typing import Callable

from tgwatcher.signal_engine import SignalEngine
from tgwatcher.tz_utils import utc_now

logger = logging.getLogger(__name__)


class SignalService:
    def __init__(self, signal_engine: SignalEngine, config: dict,
                 on_status_change: Callable | None = None):
        self._engine = signal_engine
        self._config = config
        self._on_status_change = on_status_change
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._status = {
            "running": False,
            "total": 0,
            "processed": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
            "started_at": None,
            "finished_at": None,
        }

    @property
    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _update_status(self, **kwargs) -> None:
        with self._lock:
            self._status.update(kwargs)
            snapshot = dict(self._status)
        if self._on_status_change:
            try:
                self._on_status_change(snapshot)
            except Exception:
                pass

    def start(self, chat_id: int | None = None, overwrite: bool = False,
              date_from: str | None = None, date_to: str | None = None) -> bool:
        with self._lock:
            if self._status["running"]:
                return False
        self._stop_event.clear()
        self._update_status(
            running=True,
            total=0,
            processed=0,
            completed=0,
            failed=0,
            skipped=0,
            errors=[],
            started_at=utc_now().isoformat(),
            finished_at=None,
        )
        thread = threading.Thread(
            target=self._run_loop,
            args=(chat_id, overwrite, date_from, date_to),
            daemon=True,
        )
        thread.start()
        logger.info(
            "Signal service started (chat_id=%s, overwrite=%s, date_from=%s, date_to=%s)",
            chat_id, overwrite, date_from, date_to,
        )
        return True

    def stop(self) -> bool:
        with self._lock:
            if not self._status["running"]:
                return False
        self._stop_event.set()
        self._update_status(errors=["Stopping..."])
        logger.info("Signal service stop requested")
        return True

    def _run_loop(self, chat_id: int | None, overwrite: bool,
                  date_from: str | None, date_to: str | None) -> None:
        try:
            result = self._engine.process_batch(
                chat_id=chat_id,
                overwrite=overwrite,
                date_from=date_from,
                date_to=date_to,
                progress_callback=self._progress_callback,
                stop_check=lambda: self._stop_event.is_set(),
            )
            self._update_status(
                total=result.total,
                processed=result.total,
                completed=result.completed,
                failed=result.failed,
                skipped=result.skipped,
                errors=result.errors[:10],
                finished_at=utc_now().isoformat(),
            )
        except Exception as e:
            logger.error("Signal service error: %s", e)
            self._update_status(errors=[str(e)], finished_at=utc_now().isoformat())
        finally:
            self._stop_event.set()
            self._update_status(running=False)

    def _progress_callback(self, processed: int, total: int, errors: int) -> None:
        self._update_status(processed=processed, total=total, failed=errors)

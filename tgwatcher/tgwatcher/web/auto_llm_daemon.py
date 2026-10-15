"""Auto-LLM daemon — chains LLM batch analysis + digest generation after crawl.

Triggered by AutoPollDaemon after each successful crawl tick. Runs
SignalEngine.process_batch on pending messages, then generates a digest
if the last digest is older than `auto_digest_interval_minutes` (default 60).

Lifecycle mirrors AutoPollDaemon:
  * _trigger_event: set by AutoPollDaemon.trigger_after_crawl() — wakes the
    loop to run LLM+digest immediately after crawl completes.
  * _shutdown: process-lifecycle shutdown signal (atexit/SIGTERM).
  * _last_digest_at: timestamp of last successful digest, used for 60min
    rate-limit (avoids wasting LLM calls when no new signals arrived).

SSE events pushed:
  * llm_batch_start — when LLM batch begins
  * llm_batch_done — when LLM batch completes (with counts)
  * digest_ready — when a new digest is generated
  * llm_batch_error / digest_error — on failures (loop continues)
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from tgwatcher.tz_utils import utc_now

logger = logging.getLogger(__name__)


class AutoLlmDaemon:
    """Chains LLM batch + digest generation, triggered after crawl.

    State model:
      * _trigger_event: set by trigger_after_crawl() — wakes run_loop.
        Cleared after the LLM batch starts (so a second trigger during
        a running batch is not lost — it stays set until next loop iter).
      * _shutdown: process-lifecycle signal. Terminates the daemon thread.
      * _last_digest_at: UTC datetime of last successful digest. None means
        "never generated" — first trigger will generate immediately.
      * _digest_interval: timedelta, default 60min. Configurable via
        signal.auto_digest_interval_minutes.
      * _min_signals: int, default 5. If pending count below this, skip
        LLM batch (avoid 1-message API calls). Configurable via
        signal.auto_llm_min_signals.
    """

    def __init__(
        self,
        storage: Any,
        signal_engine: Any,
        push_sse_event: Any = None,
        digest_interval_minutes: int = 60,
        min_signals: int = 5,
    ) -> None:
        self._storage = storage
        self._engine = signal_engine
        self._push_sse_event = push_sse_event or (lambda *a, **kw: None)
        self._digest_interval = timedelta(minutes=digest_interval_minutes)
        self._min_signals = min_signals

        self._trigger_event = threading.Event()
        self._shutdown = threading.Event()
        self._running = threading.Event()  # set while run_loop is alive
        self._batch_running = threading.Event()  # set only during _run_llm_batch
        self._thread: threading.Thread | None = None
        self._last_digest_at: datetime | None = None
        self._last_batch_at: datetime | None = None
        self._last_batch_count: int | None = None
        self._status_lock = threading.Lock()

    # --- dependency injection ---
    def set_sse_push_callback(self, callback: Any) -> None:
        """Wire in push_sse_event (called after SSE bus is up)."""
        self._push_sse_event = callback

    # --- lifecycle ---
    def trigger_after_crawl(self) -> None:
        """Wake the loop after a crawl completes. Idempotent — safe to call
        multiple times; the loop processes once then clears the event."""
        self._trigger_event.set()

    def signal_shutdown(self) -> None:
        """Terminate the daemon thread (atexit/SIGTERM)."""
        self._shutdown.set()
        # Wake the loop in case it's blocked on trigger_event.wait
        self._trigger_event.set()

    def is_shutdown_set(self) -> bool:
        return self._shutdown.is_set()

    # --- main loop ---
    def run_loop(self) -> None:
        """Daemon thread: wait for trigger, run LLM batch, maybe digest.

        Trigger can come from:
          1. AutoPollDaemon.trigger_after_crawl() after a crawl tick
          2. 60min timeout — fallback in case no crawl happens but pending
             messages exist (e.g. real-time listener pushed new messages)

        On shutdown: exits cleanly mid-batch via stop_check.
        """
        logger.info(
            "Auto-LLM daemon started (digest_interval=%s, min_signals=%d)",
            self._digest_interval, self._min_signals,
        )
        self._running.set()
        try:
            while not self._shutdown.is_set():
                # Wait for trigger or 60min timeout (fallback for listener-pushed msgs)
                triggered = self._trigger_event.wait(timeout=60)
                if self._shutdown.is_set():
                    break
                self._trigger_event.clear()
                try:
                    self._run_llm_batch()
                    if not self._shutdown.is_set():
                        self._maybe_generate_digest()
                except Exception as e:
                    logger.exception("Auto-LLM loop error: %s", e)
                    self._push_sse("llm_batch_error", {"error": str(e)})
        finally:
            self._running.clear()

    def _run_llm_batch(self) -> None:
        """Run SignalEngine.process_batch on all pending messages.

        Skips if pending count < min_signals (avoid tiny LLM calls).
        Pushes llm_batch_start / llm_batch_done SSE events.
        """
        # Count pending — skip if too few
        pending_count = self.count_pending()
        if pending_count < self._min_signals:
            logger.info(
                "Auto-LLM: skipping (pending=%d < min=%d)",
                pending_count, self._min_signals,
            )
            return

        self._push_sse("llm_batch_start", {"pending_count": pending_count})
        t0 = time.time()
        logger.info("Auto-LLM: starting batch (%d pending)", pending_count)

        self._batch_running.set()
        try:
            result = self._engine.process_batch(
                stop_check=self._shutdown.is_set,
            )
        finally:
            self._batch_running.clear()
        duration_ms = int((time.time() - t0) * 1000)

        with self._status_lock:
            self._last_batch_at = utc_now().replace(tzinfo=None)
            self._last_batch_count = result.completed

        self._push_sse("llm_batch_done", {
            "total": result.total,
            "completed": result.completed,
            "failed": result.failed,
            "skipped": result.skipped,
            "duration_ms": duration_ms,
        })
        logger.info(
            "Auto-LLM: batch done (completed=%d, failed=%d, skipped=%d, %dms)",
            result.completed, result.failed, result.skipped, duration_ms,
        )

    def _maybe_generate_digest(self) -> None:
        """Generate digest if last digest is older than _digest_interval.

        Skips if:
          * _digest_interval is zero (auto-digest disabled)
          * last digest was less than _digest_interval ago
        """
        if self._digest_interval.total_seconds() <= 0:
            return  # auto-digest disabled

        now = utc_now().replace(tzinfo=None)
        if self._last_digest_at is not None:
            age = now - self._last_digest_at
            if age < self._digest_interval:
                logger.info(
                    "Auto-digest: skipping (last %s ago < %s)",
                    age, self._digest_interval,
                )
                return

        # Acquire the shared digest lock — non-blocking. If the user clicked
        # "generate digest" on the web UI at the same moment, skip this run;
        # the 60min rate limit will trigger another attempt next tick.
        try:
            from tgwatcher.web.api.routes_digest import _digest_lock
        except Exception:
            _digest_lock = None  # routes_digest not importable — proceed unlocked

        acquired = False
        if _digest_lock is not None:
            try:
                acquired = _digest_lock.acquire(blocking=False)
            except Exception:
                acquired = False
            if not acquired:
                logger.info("Auto-digest: skipping (another generation in progress)")
                return

        try:
            from tgwatcher.digest import generate_digest
            result = generate_digest(self._storage, self._engine._llm)
            self._last_digest_at = now
            self._push_sse("digest_ready", {
                "id": result.id,
                "signal_count": result.signal_count,
                "from_at": result.from_at.isoformat() if result.from_at else None,
                "to_at": result.to_at.isoformat() if result.to_at else None,
                "summary_preview": (result.summary or "")[:200],
            })
            logger.info(
                "Auto-digest: generated (id=%s, signals=%d)",
                result.id, result.signal_count,
            )
        except Exception as e:
            logger.exception("Auto-digest failed: %s", e)
            self._push_sse("digest_error", {"error": str(e)})
        finally:
            if acquired and _digest_lock is not None:
                try:
                    _digest_lock.release()
                except Exception:
                    pass

    # --- helpers ---
    def count_pending(self) -> int:
        """Count messages that need LLM processing.

        Two sources:
          1. signal_factors rows with llm_status='pending' AND filter_result='passed'
             (already filtered, waiting for LLM)
          2. messages with no signal_factors row at all (newly crawled, not
             filtered yet) — these will be filtered + processed by
             SignalEngine.process_batch's get_unprocessed_messages path.

        Without source #2, the daemon skips crawl batches that brought new
        messages because count_pending returns 0 — the new messages sit in
        the messages table unprocessed forever.
        """
        from sqlalchemy import text
        try:
            with self._storage.get_session() as sess:
                # Source 1: pending/failed signal_factors (failed = retryable LLM errors)
                r1 = sess.execute(text(
                    "SELECT COUNT(*) FROM signal_factors "
                    "WHERE llm_status IN ('pending', 'failed') AND filter_result='passed'"
                ))
                pending_sf = int(r1.fetchone()[0])
                # Source 2: messages without any signal_factors row
                # (LEFT JOIN ... WHERE sf.id IS NULL). Skips deleted/empty-text
                # rows to match get_unprocessed_messages semantics.
                r2 = sess.execute(text(
                    "SELECT COUNT(*) FROM messages m "
                    "LEFT JOIN signal_factors sf "
                    "  ON m.message_id=sf.message_id AND m.chat_id=sf.chat_id "
                    "WHERE sf.id IS NULL "
                    "  AND m.is_deleted=0 AND m.text IS NOT NULL "
                    "  AND LENGTH(m.text) >= 10"
                ))
                unprocessed_msgs = int(r2.fetchone()[0])
                return pending_sf + unprocessed_msgs
        except Exception as e:
            logger.warning("Auto-LLM: count_pending failed: %s", e)
            return 0

    def _push_sse(self, event_type: str, data: dict) -> None:
        """Safe SSE push — never crashes the loop on callback error."""
        try:
            self._push_sse_event(event_type, data)
        except Exception as e:
            logger.warning("Auto-LLM SSE push failed (%s): %s", event_type, e)

    # --- test accessors ---
    @property
    def last_digest_at(self) -> datetime | None:
        return self._last_digest_at

    def set_last_digest_at(self, ts: datetime) -> None:
        """Test hook — pretend a digest was generated at this time."""
        self._last_digest_at = ts

    def get_status(self) -> dict:
        """Snapshot of daemon state for /api/signal/daemon.

        All five fields are read under _status_lock for a consistent view.
        Datetime fields are formatted as ISO UTC with 'Z' suffix; None if
        no batch / digest has run yet.
        """
        with self._status_lock:
            last_batch_at = self._last_batch_at
            last_batch_count = self._last_batch_count
            last_digest_at = self._last_digest_at
        running = self._running.is_set() and not self._shutdown.is_set()
        pending = self.count_pending()

        def _iso_z(ts: datetime | None) -> str | None:
            if ts is None:
                return None
            return ts.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

        return {
            "running": running,
            "batch_running": self._batch_running.is_set(),
            "pending": pending,
            "last_batch_at": _iso_z(last_batch_at),
            "last_batch_count": last_batch_count,
            "last_digest_at": _iso_z(last_digest_at),
        }

"""Auto-poll daemon — per-group periodic incremental crawl trigger.

Encapsulates the 4 module-level globals (_auto_poll_state, _auto_poll_lock,
_auto_poll_stop_requested, _auto_poll_shutdown) and their associated
functions (_init_auto_poll, _auto_poll_loop) that previously lived in
tgwatcher.web.api. Behavior is preserved verbatim — only the location changed.

Module-level shims in api.py keep backward-compat for:
  - tests/test_bugfix_2026_07_24.py: imports `_auto_poll_loop`, `_auto_poll_shutdown`
  - tests/test_metrics.py + web/metrics.py: reads `api_mod._auto_poll_state`
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class AutoPollDaemon:
    """Encapsulates auto-poll daemon state and the daemon loop.

    State model (preserved from previous module-level globals):
      * _state: dict[chat_id, {enabled, interval, next_tick_at, name}]
      * _lock: guards _state
      * _stop_requested: set by stop_crawl() so the daemon suspends triggering
        new crawls after a user-initiated stop. Cleared when auto-poll is
        re-enabled via update_chat_config(enabled=True) or on fresh startup.
        Distinct from the CrawlService stop event (which halts the
        currently-running crawl); this one halts the daemon that would *start*
        the next crawl.
      * _shutdown: process-lifecycle shutdown signal. Distinct from
        _stop_requested (user clicks "stop auto-poll" — reversible, keeps
        daemon alive); this one terminates the daemon thread itself on
        atexit/SIGTERM.
    """

    def __init__(self) -> None:
        self._state: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._stop_requested: threading.Event = threading.Event()
        self._shutdown: threading.Event = threading.Event()
        # Crawl service is wired in by api.py at init_services time — set as
        # None here, populated via set_crawl_service() before the loop starts.
        self._crawl_service: Any = None
        # SSE push callback wired in by api.py (avoids circular import).
        # When None, the loop skips SSE push for auto_poll_tick events.
        self._push_sse_event: Any = None
        # Auto-LLM daemon — wired in by api.py at init_services time.
        # When set, trigger_after_crawl() is called after each successful
        # crawl tick so LLM batch + digest run chain-style.
        self._auto_llm_daemon: Any = None

    # --- dependency injection ---
    def set_crawl_service(self, crawl_service: Any) -> None:
        """Wire in the CrawlService instance (called by init_services)."""
        self._crawl_service = crawl_service

    def set_auto_llm_daemon(self, daemon: Any) -> None:
        """Wire in the AutoLlmDaemon instance (called by init_services).

        When set, each successful crawl tick triggers
        daemon.trigger_after_crawl() so the LLM batch + digest chain runs
        immediately after crawl completion.
        """
        self._auto_llm_daemon = daemon

    # --- lifecycle hooks (replace module-level functions) ---
    def init_from_config(self, config: dict) -> None:
        """Populate _state from config (called at startup).

        Replaces _init_auto_poll(config). Clears any stale stop signal from a
        previous run, then rebuilds _state from config['groups'].
        """
        self._stop_requested.clear()
        with self._lock:
            self._state.clear()
            now = time.time()
            for g in config.get("groups", []):
                gid = g.get("id")
                if not gid:
                    continue
                self._state[gid] = {
                    "enabled": bool(g.get("auto_poll", False)),
                    "interval": int(g.get("poll_interval_seconds", 15)),
                    "next_tick_at": now + int(g.get("poll_interval_seconds", 15)),
                    "name": g.get("name", str(gid)),
                }

    def run_loop(self) -> None:
        """Daemon thread: every 1s, scan _state for due ticks and fire incremental crawl.

        Replaces _auto_poll_loop(). Exits when _shutdown is set (atexit/SIGTERM).
        Uses wait(1) as the per-tick sleep so shutdown wakes the loop immediately
        rather than blocking up to 1s. The wait is at the top of the loop body so
        all paths (including the "no due ticks" early-continue) sleep — preventing
        a busy loop.
        """
        logger.info("Auto-poll daemon started")
        while not self._shutdown.is_set():
            # Top-of-loop wait: blocks up to 1s, returns True immediately if
            # shutdown is set. Prevents busy-loop on empty _state.
            if self._shutdown.wait(1):
                break
            try:
                # User clicked stop — suspend triggering new crawls. Do NOT reset
                # next_tick_at here: the tick stays due so that when the user
                # re-enables auto-poll (clearing the Event), the next crawl fires
                # immediately rather than waiting a full interval.
                if self._stop_requested.is_set():
                    continue
                now = time.time()
                # Skip if any crawl currently running — leave next_tick_at untouched so
                # the tick fires as soon as the running crawl finishes (no lost tick).
                cs = self._crawl_service
                if cs and cs.status.get("running"):
                    continue
                with self._lock:
                    due = [
                        (cid, s) for cid, s in self._state.items()
                        if s["enabled"] and now >= s["next_tick_at"]
                    ]
                    if not due:
                        continue
                    # Trigger only the most-due group; reschedule all due so they don't pile up.
                    due.sort(key=lambda cs_item: cs_item[1]["next_tick_at"])
                    cid, s = due[0]
                    s["next_tick_at"] = now + s["interval"]
                    # Other due groups: reschedule to next cycle too (avoid back-to-back stacking)
                    for other_cid, other_s in due[1:]:
                        other_s["next_tick_at"] = now + other_s["interval"]
                logger.info(
                    "Auto-poll: triggering incremental crawl",
                    extra={"chat_id": cid, "chat_name": s.get("name"), "action": "crawl_start"},
                )
                try:
                    if cs:
                        cs.start(mode="incremental", group_id=cid)
                except Exception as e:
                    logger.warning("Auto-poll crawl start failed", extra={"chat_id": cid, "error": str(e)})
                # Chain to auto-LLM daemon — triggers LLM batch + digest
                # after crawl completes. Safe to call even if daemon is None
                # or crawl start failed (LLM would just find no new pending).
                if self._auto_llm_daemon is not None:
                    try:
                        self._auto_llm_daemon.trigger_after_crawl()
                    except Exception as e:
                        logger.warning("Auto-poll trigger_after_crawl failed: %s", e)
                # SSE push is performed by api.py via the push_sse_event shim —
                # call the bound callback if set. We avoid importing push_sse_event
                # directly to keep this module decoupled from the SSE bus.
                if self._push_sse_event is not None:
                    try:
                        self._push_sse_event("auto_poll_tick", {
                            "chat_id": cid,
                            "name": s.get("name"),
                            "next_tick_at": s["next_tick_at"],
                            "interval": s["interval"],
                        })
                    except Exception as e:
                        logger.warning("Auto-poll SSE push failed: %s", e)
            except Exception as e:
                logger.warning("Auto-poll loop error: %s", e)

    # --- stop/shutdown event wrappers ---
    def request_stop(self) -> None:
        """Set _stop_requested — suspends triggering new crawls after user stop."""
        self._stop_requested.set()

    def clear_stop(self) -> None:
        """Clear _stop_requested — resume triggering ticks (re-enable path)."""
        self._stop_requested.clear()

    def is_stop_requested(self) -> bool:
        return self._stop_requested.is_set()

    def signal_shutdown(self) -> None:
        """Set _shutdown — terminate the daemon thread on atexit/SIGTERM."""
        self._shutdown.set()

    def is_shutdown_set(self) -> bool:
        return self._shutdown.is_set()

    def clear_shutdown(self) -> None:
        """Clear _shutdown (used by tests that start/stop the loop)."""
        self._shutdown.clear()

    def wait_shutdown(self, timeout: float | None = None) -> bool:
        """Wait for _shutdown to be set. Mirrors threading.Event.wait semantics."""
        return self._shutdown.wait(timeout)

    # --- state accessors (for endpoints) ---
    def get_state_snapshot(self) -> dict[int, dict]:
        """Return a shallow copy of _state under _lock (for read-only inspection)."""
        with self._lock:
            return {cid: dict(s) for cid, s in self._state.items()}

    def update_chat_config(
        self,
        chat_id: int,
        *,
        enabled: bool | None = None,
        interval: int | None = None,
        name: str | None = None,
    ) -> dict:
        """Update a single chat's state in place under _lock.

        Replaces the inline `with _auto_poll_lock:` block in update_auto_poll.
        Returns the updated state dict for the chat. If the chat is missing
        from _state, creates a fresh entry (preserving prior behavior).
        """
        with self._lock:
            s = self._state.get(chat_id)
            if s is None:
                s = {"name": name or str(chat_id)}
                self._state[chat_id] = s
            if enabled is not None:
                s["enabled"] = bool(enabled)
            if interval is not None:
                s["interval"] = interval
            s["next_tick_at"] = time.time() + s["interval"]
            if name is not None:
                s["name"] = name
            return dict(s)

    # --- SSE push callback (injected by api.py to avoid circular import) ---
    def set_sse_push_callback(self, callback: Any) -> None:
        """Wire in push_sse_event from api.py (called after SSE bus is up)."""
        self._push_sse_event = callback

    # Backward-compat: allow direct attribute access for legacy callers
    # (metrics.py + tests read `api_mod._auto_poll_state`). api.py exposes
    # `_auto_poll_state` as a property on the module via a shim, so this
    # accessor is only used internally by tests that bypass the shim.
    @property
    def state(self) -> dict[int, dict]:
        """Direct access to the live state dict (no copy). Use with caution.

        Exposed for backward-compat with metrics.py which reads
        `getattr(_api, "_auto_poll_state", {})`. api.py's module-level shim
        returns this property.
        """
        return self._state

    @property
    def lock(self) -> threading.Lock:
        """Direct access to _lock for backward-compat callers that need `with _lock:`."""
        return self._lock

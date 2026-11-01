"""Real-time Telethon listener daemon for TGWatcher.

Encapsulates the listener state that was previously held as 4 module-level
globals in `tgwatcher/web/api.py`. Extracted in Phase 2A of the 3-file
refactor (plan: ticklish-cooking-glade.md).

Contract:
- `init_from_config(config)` — replaces `_init_listener`: decides which
  groups to listen and calls `start_thread`.
- `start_thread(listen_groups) -> bool` — replaces `_start_listener_thread`:
  schedules `run_async` on the shared async loop.
- `run_async(listen_groups)` — replaces `_run_listener_async` (async):
  runs `start_listener` on the shared TGClient.
- `stop() -> bool` — replaces `_stop_listener`: signals stop via the
  asyncio.Event using `call_soon_threadsafe`.
- Properties: `is_running` (bool), `stop_event` (the asyncio.Event or None).

The daemon does NOT own the TGClient, async loop, storage, signal engine,
or SSE bus — those remain in api.py as module-level singletons and are
accessed at call time via the `_host` reference (late binding to avoid
circular imports). This mirrors the pre-refactor behavior where the
listener functions accessed these as module globals looked up at call
time.

Threading model (preserved exactly):
- The listener coroutine runs on the shared `_async_loop` thread (same
  loop as crawl_service), so it shares the TGClient and event loop
  without conflict.
- `_stop_event` is an `asyncio.Event` created INSIDE the runner coroutine
  on the async loop thread. `stop()` uses `loop.call_soon_threadsafe()`
  to signal it from any thread.
- `_tg_client_guard()` is called synchronously in `start_thread` BEFORE
  scheduling the coroutine, to avoid racing with other loop coroutines
  and to prevent "database is locked" on telethon's SQLite session.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ListenerDaemon:
    """Manages the real-time Telethon NewMessage listener lifecycle.

    All state is held inside the instance — no module globals. api.py
    owns a singleton `_listener_daemon = ListenerDaemon()`.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event: "asyncio.Event | None" = None
        self._running: bool = False
        self._lock = threading.Lock()
        # Late-binding host accessor. Set by api.py at module init time to
        # a callable returning the api module (so we can read _async_loop,
        # _tg_client_guard, _get_tg_client, _storage, _signal_engine,
        # push_sse_event at call time without a circular import).
        self._host_getter: Callable[[], Any] | None = None

    def bind_host(self, host_getter: Callable[[], Any]) -> None:
        """Bind a callable returning the api module (late binding).

        api.py calls this at module init to break the circular import:
        `listener_daemon` imports nothing from `api`, but `api` imports
        `ListenerDaemon`. The callable is invoked at call time so the
        daemon always sees the current api module state.
        """
        self._host_getter = host_getter

    @property
    def _host(self) -> Any:
        if self._host_getter is None:
            return None
        return self._host_getter()

    # --- Public API ---

    def init_from_config(self, config: dict) -> None:
        """Start the real-time listener if any group has auto_listen=true.

        Called from `init_services` at startup.
        """
        listen_groups = _get_listen_groups(config)
        if not listen_groups:
            logger.info("Listener: no groups with auto_listen=true, skipping startup")
            return
        self.start_thread(listen_groups)

    def start_thread(self, listen_groups: list[dict]) -> bool:
        """Schedule `start_listener` on the shared `_async_loop`.

        Returns False if already running, or if the async loop is unavailable,
        or if TGClient connect fails.

        The listener coroutine runs on the `_async_loop` thread (same loop
        as crawl_service), so it shares the TGClient and event loop without
        conflict.
        """
        host = self._host
        with self._lock:
            if self._running:
                logger.warning("Listener: already running, ignoring start request")
                return False
            loop = host._async_loop.get_loop() if host is not None and host._async_loop else None
            if loop is None:
                logger.error("Listener: no async loop available")
                return False

            # Synchronously ensure TGClient is connected (holds _tg_lock via guard).
            # Done outside the _async_loop to avoid racing with other loop coroutines
            # and to prevent "database is locked" on telethon's SQLite session.
            try:
                with host._tg_client_guard() as _tg:
                    pass  # connect only; tg is yielded but unused here
            except Exception as e:
                logger.error("Listener: TGClient connect failed: %s", e, exc_info=True)
                host.push_sse_event("listener_status", {"enabled": False, "error": f"connect failed: {e}"})
                return False

            async def _runner():
                # Create stop_event FIRST so stop() can signal even during
                # listener startup.
                self._stop_event = asyncio.Event()
                try:
                    await self.run_async(listen_groups)
                except Exception as e:
                    logger.error("Listener coroutine crashed: %s", e, exc_info=True)
                    with self._lock:
                        self._running = False
                    host.push_sse_event("listener_status", {"enabled": False, "error": str(e)})

            # Schedule the coroutine on the shared _async_loop thread
            asyncio.run_coroutine_threadsafe(_runner(), loop)
            self._running = True
            # _thread stays None — we don't own a thread; runs on _async_loop thread
            logger.info("Listener coroutine scheduled for %d groups", len(listen_groups))
            host.push_sse_event(
                "listener_status",
                {"enabled": True, "groups": [g.get("name", g.get("id")) for g in listen_groups]},
            )
            return True

    async def run_async(self, listen_groups: list[dict]) -> None:
        """Run `start_listener` on the shared TGClient.

        Stops when `stop_event` is set.

        Pre-condition: TGClient must be connected before this is called
        (via `_tg_client_guard` in the caller thread) to avoid racing
        with other telethon session writers.
        """
        host = self._host
        from tgwatcher.listener import start_listener
        tg = host._get_tg_client()
        if tg.client is None or not tg.client.is_connected():
            logger.error("Listener: TGClient not connected on entry; caller must connect first")
            return
        try:
            await start_listener(
                tg, host._storage, listen_groups,
                on_new_message=host.push_new_message,
                signal_engine=host._signal_engine,
                stop_event=self._stop_event,
            )
        finally:
            with self._lock:
                self._running = False
            logger.info("Listener thread exiting")
            host.push_sse_event("listener_status", {"enabled": False})

    def stop(self) -> bool:
        """Signal the listener to stop. Returns True if signal was sent.

        The runner's `run_async` races connect against `stop_event.wait()`,
        so stop during connect phase still aborts.
        """
        with self._lock:
            if not self._running or self._stop_event is None:
                return False
            host = self._host
            loop = host._async_loop.get_loop() if host is not None and host._async_loop else None
            if loop is None:
                return False
            loop.call_soon_threadsafe(self._stop_event.set)
            return True

    # --- Observability / test access ---

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stop_event(self) -> "asyncio.Event | None":
        return self._stop_event


def _get_listen_groups(config: dict) -> list[dict]:
    """Return groups with `auto_listen=true`.

    Kept as a module-level helper (not a method) because it is a pure
    function over the config dict — no instance state needed.
    """
    return [g for g in config.get("groups", []) if g.get("auto_listen", False)]

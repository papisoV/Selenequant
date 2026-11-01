"""SSE event bus for TGWatcher.

Encapsulates the SSE pub/sub state. Per-listener ``queue.Queue``
design (optimization #5, 2026-07-25) replaces the older shared-lock +
threading.Event pattern — push() no longer holds a shared lock during
per-listener notification, slow listeners don't block others, and the
generator loop is a simple blocking ``queue.get(timeout=30)``.

Contract:
- `push(event_type, data)` — append event, put to each listener's queue
- `register_listener(last_id)` — returns `(queue.Queue, adjusted_last_id)`;
  handles fresh-connection and buffer-rollover cases. Initial replay
  events are pre-loaded into the queue.
- `unregister_listener(listener_q)` — removes listener
- `events_since(last_id)` — kept for backward-compat callers (returns
  events with id > last_id from the ring buffer)
- `trim_if_needed()` — kept for backward-compat; buffer is now trimmed
  inline during push()
- Properties: `current_event_id`, `oldest_event_id`, `listener_count`,
  `buffered_event_count` — for metrics/observability

The bus is thread-safe. All state is held inside the instance — no module
globals. api.py owns a singleton `_sse_bus = SSEBus()`.
"""
from __future__ import annotations

import logging
import queue
import threading

logger = logging.getLogger(__name__)

MAX_SSE_EVENTS = 200
MAX_LISTENER_QUEUE = 1024  # per-listener bound; ~8x ring buffer size


class SSEBus:
    """Thread-safe pub/sub bus for SSE events.

    Each listener owns a `queue.Queue(maxsize=MAX_LISTENER_QUEUE)`.
    push() snapshots the listener list under a brief lock, then puts
    to each queue outside the lock — O(1) per listener, no contention
    between listeners. Slow listeners drop events (queue full) and
    recover via Last-Event-ID replay on reconnect.
    """

    def __init__(self, max_events: int = MAX_SSE_EVENTS) -> None:
        self._lock = threading.Lock()  # guards _listeners, _events, _event_id
        self._listeners: list[queue.Queue] = []
        self._events: list[dict] = []  # ring buffer for replay only
        self._event_id: int = 0
        self._max_events = max_events

    # --- Pub ---

    def push(self, event_type: str, data: dict) -> None:
        """Append event, put to each listener's queue. Thread-safe."""
        with self._lock:
            self._event_id += 1
            event = {"id": self._event_id, "type": event_type, "data": data}
            self._events.append(event)
            if len(self._events) > self._max_events:
                del self._events[: len(self._events) - self._max_events]
            listeners = list(self._listeners)  # snapshot under lock
        # O(1) per listener — no shared lock held during put
        for q in listeners:
            try:
                q.put_nowait(event)
            except queue.Full:
                # Slow listener — drop. Client recovers via Last-Event-ID
                # replay on reconnect. Log includes queue size for diagnosis.
                logger.warning(
                    "SSE listener queue full, dropping event",
                    extra={
                        "listener_queue_size": q.qsize(),
                        "event_id": event["id"],
                        "action": "sse_drop",
                    },
                )

    # --- Sub ---

    def register_listener(self, last_id: int) -> tuple[queue.Queue, int]:
        """Register a new SSE listener.

        Returns (listener_queue, adjusted_last_id). The caller must call
        unregister_listener when done. Handles three cases:
        - last_id == 0: fresh connection, start from current max (no replay)
        - last_id < oldest buffered: buffer rolled past; start from oldest-1
          to avoid stall (client will miss intermediate events)
        - otherwise: normal replay path — events > last_id pre-loaded into queue
        """
        q: queue.Queue = queue.Queue(maxsize=MAX_LISTENER_QUEUE)
        with self._lock:
            self._listeners.append(q)
            if last_id == 0:
                last_id = self._event_id
            elif self._events and last_id < self._events[0]["id"]:
                logger.warning(
                    "SSE reconnect: last_id=%d older than buffer floor=%d; "
                    "client will miss intermediate events. Use webhook + "
                    "/api/signals/export?since=<ts> for full compensation.",
                    last_id, self._events[0]["id"],
                )
                last_id = self._events[0]["id"] - 1
            # Initial replay: push events > last_id into the new queue
            for e in self._events:
                if e["id"] > last_id:
                    try:
                        q.put_nowait(e)
                    except queue.Full:
                        # Queue pre-filled to capacity — listener will drain
                        # and continue receiving new events via push().
                        break
        return q, last_id

    def unregister_listener(self, listener_q: queue.Queue) -> None:
        with self._lock:
            if listener_q in self._listeners:
                self._listeners.remove(listener_q)

    def events_since(self, last_id: int) -> list[dict]:
        """Return events with id > last_id. Kept for backward-compat callers."""
        with self._lock:
            return [e for e in self._events if e["id"] > last_id]

    def trim_if_needed(self) -> None:
        """Secondary buffer trim — kept for backward-compat. Buffer is now
        trimmed inline during push() so this is a no-op."""
        # No-op: push() trims inline. Kept so external callers (if any)
        # don't break.
        return

    # --- Observability (for /api/metrics) ---

    @property
    def listener_count(self) -> int:
        return len(self._listeners)

    @property
    def buffered_event_count(self) -> int:
        return len(self._events)

    @property
    def current_event_id(self) -> int:
        return self._event_id

    @property
    def oldest_event_id(self) -> int | None:
        """Return the oldest buffered event's id, or None if buffer empty.
        Used for reconnect diagnostics."""
        with self._lock:
            return self._events[0]["id"] if self._events else None

    @property
    def max_events(self) -> int:
        return self._max_events

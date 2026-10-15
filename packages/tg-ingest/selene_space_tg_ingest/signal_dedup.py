"""In-memory signal deduplication for downstream-facing push.

Same news forwarded across multiple groups (or re-polled from RSS feeds)
within a short window should only trigger one downstream push — otherwise
Selene would ingest duplicate factors / place duplicate orders.

Key concept:
- key = (sorted_symbols, event_type)  (US-006: direction_sign removed — the
  same news forwarded by sources that signed it differently is one event)
- Neutral events (direction = 0) are event-sensor observations, never
  duplicates of one another — they bypass the key cache entirely (US-006)
- Within `window_seconds` (default 300 = 5 min), only the first signal per key
  is pushed. A later signal with HIGHER confidence supersedes — it gets pushed
  once (the previously-pushed lower-confidence one can't be un-pushed, but
  the higher-confidence one is more actionable).

In-memory only. Cache is lost on restart — acceptable: worst case is a few
duplicate pushes in the first minutes after restart, no trading logic impact.

Not persisted to DB — dedup is a push-filter, not data state. All signals
still land in signal_factors table with is_signal=True regardless of dedup.

Ported from tgwatcher/tgwatcher/signal_dedup.py (2026-08-10) into tg-ingest.
Adaptation: `_make_key` also accepts symbols as a JSON string (the tg-ingest
factor payload shape), not just a Python list.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class SignalDeduper:
    """In-memory dedup cache for downstream-facing signal push.

    Thread-safety: SignalEngine.process_new_message runs from listener
    thread. Storage push is single-threaded per chat, but multiple chats
    could process concurrently. Locks guard the cache.
    """

    def __init__(self, window_seconds: int = 300) -> None:
        self._window = window_seconds
        # key -> list of (timestamp, payload) tuples within the window
        self._cache: dict[tuple, list[tuple[float, dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def should_emit(self, payload: dict[str, Any]) -> bool:
        """Return True if this signal should be pushed downstream.

        Args:
            payload: The signal payload built by SignalEngine._build_signal_payload.

        Returns:
            True if the signal should be pushed (first in window, or higher
            confidence than the existing top). False if deduplicated.
        """
        if self._window <= 0:
            return True

        # Neutral events (direction = 0) are event-sensor observations, not
        # duplicates of each other — bypass the key cache so two distinct
        # same-type neutrals within the window both emit (US-006).
        if not payload.get("direction"):
            return True

        key = self._make_key(payload)
        now = time.time()
        cutoff = now - self._window

        with self._lock:
            # Purge expired entries for this key
            existing = [(ts, p) for ts, p in self._cache.get(key, []) if ts > cutoff]
            self._cache[key] = existing

            if not existing:
                # First signal in window — emit, record
                self._cache[key].append((now, payload))
                return True

            # Has existing — compare confidence
            existing_top = max(existing, key=lambda x: x[1].get("confidence") or 0)
            new_conf = payload.get("confidence") or 0
            if new_conf > (existing_top[1].get("confidence") or 0):
                # Higher confidence — emit the new one (supersede)
                self._cache[key].append((now, payload))
                return True

            # Lower or equal confidence — dedup
            self._cache[key].append((now, payload))
            return False

    @staticmethod
    def _make_key(payload: dict[str, Any]) -> tuple:
        """Build dedup key from payload.

        Same news forwarded across groups should produce the same key:
        - symbols: sorted tuple (order-independent). Accepts a Python list or a
          JSON-encoded string (the tg-ingest factor payload shape).
        - event_type: exact match
        (US-006: direction_sign removed — one event reported with different
        direction signs must not double-push.)
        """
        symbols = payload.get("symbols") or []
        if isinstance(symbols, str):
            try:
                symbols = json.loads(symbols)
            except (ValueError, TypeError):
                symbols = []
        symbols_tuple = tuple(sorted(symbols)) if isinstance(symbols, list) else tuple()
        event_type = payload.get("event_type") or "other"
        return (symbols_tuple, event_type)

    def stats(self) -> dict[str, int]:
        """Return cache size stats for debugging/UI."""
        with self._lock:
            return {
                "keys": len(self._cache),
                "total_entries": sum(len(v) for v in self._cache.values()),
            }

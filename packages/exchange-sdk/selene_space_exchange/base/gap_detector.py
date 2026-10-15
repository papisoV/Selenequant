"""WebSocket gap detection for reliable event streaming.

Tracks last-seen event timestamps and sequence numbers so that
missed events during reconnection can be detected and filled
via REST reconciliation.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class WSGapDetector:
    """Detect missed WS events during reconnection.

    Binance and OKX both provide sequence IDs in their private WS streams.
    On reconnect, this detector checks for gaps and triggers REST
    reconciliation when events were missed.

    Usage::

        detector = WSGapDetector()

        # On each WS event:
        detector.record_event(timestamp_ms, seq_num=12345)

        # On reconnect:
        if detector.detect_gap(current_seq=13000):
            await reconciler.reconcile_on_reconnect()
    """

    def __init__(
        self,
        *,
        max_time_gap_s: float = 300.0,
        on_gap_detected: object | None = None,
    ) -> None:
        self._last_event_time: int = 0
        self._last_seq_num: int | None = None
        self._max_time_gap_s = max_time_gap_s
        self._on_gap_detected = on_gap_detected
        self._connected_at: float = 0.0

    def record_event(self, timestamp_ms: int, seq_num: int | None = None) -> None:
        """Record a received WS event.

        Args:
            timestamp_ms: Event timestamp from the exchange.
            seq_num: Exchange-provided sequence number (if available).
        """
        if timestamp_ms > self._last_event_time:
            self._last_event_time = timestamp_ms
        if seq_num is not None:
            self._last_seq_num = seq_num

    def on_connect(self) -> None:
        """Mark the moment of WS (re)connection."""
        self._connected_at = time.monotonic()

    def detect_gap(self, current_seq: int | None = None) -> bool:
        """Check if a gap exists in the event stream.

        Gap is detected if:
        1. Sequence number jumped (current > last + 1)
        2. Time gap exceeds threshold (no events for too long)

        Args:
            current_seq: The first sequence number seen after reconnect.

        Returns:
            True if a gap was detected.
        """
        gap_detected = False
        gap_reason = ""

        # Check sequence gap
        if current_seq is not None and self._last_seq_num is not None:
            if current_seq > self._last_seq_num + 1:
                gap_detected = True
                gap_reason = (
                    f"Sequence gap: expected {self._last_seq_num + 1}, "
                    f"got {current_seq} ({current_seq - self._last_seq_num - 1} events missed)"
                )

        # Check time gap
        if self._last_event_time > 0 and self._connected_at > 0:
            now_ms = int(time.time() * 1000)
            time_since_last = (now_ms - self._last_event_time) / 1000.0
            if time_since_last > self._max_time_gap_s:
                gap_detected = True
                if gap_reason:
                    gap_reason += f"; also time gap {time_since_last:.0f}s"
                else:
                    gap_reason = f"Time gap: {time_since_last:.0f}s since last event (threshold: {self._max_time_gap_s:.0f}s)"

        if gap_detected:
            logger.warning("WS gap detected: %s", gap_reason)
            if self._on_gap_detected is not None:
                try:
                    self._on_gap_detected(gap_reason)
                except Exception:
                    logger.exception("Gap detection callback failed")

        return gap_detected

    def reset(self) -> None:
        """Reset detector state (e.g. after successful reconciliation)."""
        self._last_event_time = 0
        self._last_seq_num = None
        self._connected_at = 0.0

    @property
    def last_event_time(self) -> int:
        return self._last_event_time

    @property
    def last_seq_num(self) -> int | None:
        return self._last_seq_num

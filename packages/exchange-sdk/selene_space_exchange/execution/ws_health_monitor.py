"""WsHealthMonitor — tracks market WS health and gates place_order when WS is unhealthy.

Binance 1008 rate limit / network partitions cause WS to stall. If the engine
places orders during this window, the order may be lost (no WS ack), leading
to phantom fills or duplicate orders on reconnect. The WsHealthMonitor records
heartbeat timestamps and reconnect events; place_order calls is_healthy()
before reaching the REST call, refusing to submit when WS is degraded.

Health rules:
  - last_heartbeat_age > 5s → unhealthy (stale_heartbeat)
  - reconnect_count in last 1h > 5 → unhealthy (excessive_reconnects)
  - Otherwise healthy.

Usage:
  monitor = WsHealthMonitor()
  # WS on_message handler calls:
  monitor.record_heartbeat()
  # WS on_reconnect handler calls:
  monitor.record_reconnect()
  # place_order gate:
  if not monitor.is_healthy():
      raise WsUnhealthyError(monitor.reason)
"""

from __future__ import annotations

import threading
import time
from collections import deque


class WsUnhealthyError(Exception):
    """Raised by place_order when WS health monitor reports unhealthy.

    Distinct from PreTradeRiskBlockError — risk block is about portfolio risk,
    WS unhealthy is about market data infrastructure being unavailable.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"WS unhealthy: {reason}")
        self.reason = reason


class WsHealthMonitor:
    """Thread-safe WS health tracker.

    Records heartbeats (any WS message received) and reconnects (WS
    reconnect event). is_healthy() returns False when either:
      - heartbeat is stale (>5s old), or
      - reconnect count in last 1h exceeds 5.

    The 5s/5 thresholds are deliberately conservative — Binance futures WS
    sends mark price updates every 1s, so 5s of silence strongly implies
    rate limit (1008) or network partition. 5 reconnects in 1h indicates
    chronic instability (network or cred issue), not transient blips.
    """

    HEARTBEAT_STALE_S = 5.0
    RECONNECT_WINDOW_S = 3600.0  # 1h
    RECONNECT_THRESHOLD = 5

    def __init__(self, *, now_fn=time.time) -> None:
        self._now = now_fn
        self._lock = threading.Lock()
        self._last_heartbeat_ts: float | None = None
        # deque for O(1) popleft of expired reconnects
        self._reconnect_history: deque[float] = deque()
        self._unhealthy_reason: str | None = None

    def record_heartbeat(self) -> None:
        """Call on every WS message received (ticker, depth, trade, etc.)."""
        with self._lock:
            self._last_heartbeat_ts = self._now()

    def record_reconnect(self) -> None:
        """Call when the WS client reconnects (after a 1008 drop, etc.)."""
        now = self._now()
        with self._lock:
            self._reconnect_history.append(now)
            # Trim reconnects older than the 1h window
            cutoff = now - self.RECONNECT_WINDOW_S
            while self._reconnect_history and self._reconnect_history[0] < cutoff:
                self._reconnect_history.popleft()

    @property
    def reason(self) -> str | None:
        """Return current unhealthy reason, or None if healthy."""
        self.is_healthy()  # refresh _unhealthy_reason
        return self._unhealthy_reason

    @property
    def heartbeat_age_s(self) -> float | None:
        """Phase 5 observability: seconds since last heartbeat, None if never."""
        now = self._now()
        with self._lock:
            if self._last_heartbeat_ts is None:
                return None
            return now - self._last_heartbeat_ts

    @property
    def reconnect_count_1h(self) -> int:
        """Phase 5 observability: reconnect count in last 1h window."""
        now = self._now()
        cutoff = now - self.RECONNECT_WINDOW_S
        with self._lock:
            return sum(1 for ts in self._reconnect_history if ts >= cutoff)

    def is_healthy(self) -> bool:
        """Return True if WS is healthy enough to place orders.

        Side effect: updates self._unhealthy_reason for diagnostics.
        """
        now = self._now()
        with self._lock:
            # Check heartbeat staleness first
            if self._last_heartbeat_ts is None:
                self._unhealthy_reason = "no_heartbeat_yet"
                return False
            heartbeat_age = now - self._last_heartbeat_ts
            if heartbeat_age > self.HEARTBEAT_STALE_S:
                self._unhealthy_reason = (
                    f"stale_heartbeat: {heartbeat_age:.1f}s > "
                    f"{self.HEARTBEAT_STALE_S:.0f}s"
                )
                return False
            # Check reconnect frequency
            reconnect_count = len(self._reconnect_history)
            if reconnect_count > self.RECONNECT_THRESHOLD:
                self._unhealthy_reason = (
                    f"excessive_reconnects: {reconnect_count} in last "
                    f"{int(self.RECONNECT_WINDOW_S)}s > "
                    f"{self.RECONNECT_THRESHOLD}"
                )
                return False
            self._unhealthy_reason = None
            return True

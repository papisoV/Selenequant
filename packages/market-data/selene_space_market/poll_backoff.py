"""Failure backoff + staleness tracking for poll-based kline sources.

Pattern absorbed from marketingdashboard server/lib/cache.cjs + last-good.cjs
(MIT, pattern-level port — no code copied): a failed upstream call enters an
exponential backoff window during which the key is skipped entirely (negative
cache), so a downed upstream is hit at most once per window; degraded reads
carry a staleness signal instead of silently serving old data. mrd applies
this per request-key; here it is applied per poll-source symbol.
"""

from __future__ import annotations

import logging
import time


class PollBackoff:
    """Per-symbol failure backoff and staleness bookkeeping for poll loops.

    Not thread-safe by design: poll loops and their accessors run on one
    event loop (same convention as the source caches).
    """

    def __init__(
        self,
        base_interval_sec: float,
        *,
        cap_sec: float,
        stale_after_sec: float,
    ) -> None:
        if base_interval_sec <= 0 or cap_sec <= 0 or stale_after_sec <= 0:
            raise ValueError("intervals must be positive")
        self._base = base_interval_sec
        self._cap = cap_sec
        self._stale_after = stale_after_sec
        self._fail_counts: dict[str, int] = {}
        self._last_attempt_at: dict[str, float] = {}
        self._last_success_at: dict[str, float] = {}
        self._stale_warned: set[str] = set()

    def backoff_seconds(self, fail_count: int) -> float:
        """Exponential window: base * 2**n capped at *cap_sec*."""
        return min(self._cap, self._base * 2.0**fail_count)

    def blocked(self, key: str, now: float | None = None) -> bool:
        """True while *key* sits inside its failure backoff window."""
        fails = self._fail_counts.get(key, 0)
        if not fails:
            return False
        if now is None:
            now = time.monotonic()
        return (now - self._last_attempt_at.get(key, 0.0)) < self.backoff_seconds(fails)

    def record_attempt(self, key: str, now: float | None = None) -> None:
        self._last_attempt_at[key] = time.monotonic() if now is None else now

    def record_success(self, key: str, now: float | None = None) -> None:
        """Any successful upstream call resets backoff and the stale flag."""
        self._fail_counts.pop(key, None)
        self._last_success_at[key] = time.monotonic() if now is None else now
        self._stale_warned.discard(key)

    def record_failure(self, key: str) -> None:
        self._fail_counts[key] = self._fail_counts.get(key, 0) + 1

    def fail_count(self, key: str) -> int:
        return self._fail_counts.get(key, 0)

    def staleness_seconds(self, key: str, now: float | None = None) -> int | None:
        """Seconds since the last successful call; None when never fetched."""
        last = self._last_success_at.get(key)
        if last is None:
            return None
        if now is None:
            now = time.monotonic()
        return int(now - last)

    def warn_if_stale(self, key: str, logger: logging.Logger, label: str) -> None:
        """Log once per staleness episode when the last success is old."""
        age = self.staleness_seconds(key)
        if age is None or age <= self._stale_after or key in self._stale_warned:
            return
        logger.warning(
            "%s: data for %s is stale (last success %ss ago, threshold %ss) "
            "— upstream may be degraded",
            label,
            key,
            age,
            self._stale_after,
        )
        self._stale_warned.add(key)

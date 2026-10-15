"""Data retention cleaner — deletes old messages and signal data.

Runs on startup and at a configurable daily interval. Logs count of
deleted rows. Configured via `compliance.data_retention_days` in config.yaml.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

logger = logging.getLogger(__name__)


class DataRetentionCleaner:
    """Periodically deletes data older than retention_days.

    Thread-safe. Runs in a background daemon thread.
    """

    def __init__(self, storage, retention_days: int = 365, interval_hours: int = 24) -> None:
        self._storage = storage
        self._retention_days = retention_days
        self._interval_hours = interval_hours
        self._shutdown = threading.Event()

    def run_once(self) -> dict:
        """Execute one retention pass. Returns dict with deletion counts."""
        if self._retention_days <= 0:
            logger.info("Data retention disabled (retention_days=%d)", self._retention_days)
            return {"messages": 0, "signal_factors": 0}

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=self._retention_days)
        counts = {}

        try:
            with self._storage.get_session() as sess:
                # Delete signal_factors for old messages
                result = sess.execute(text(
                    "DELETE FROM signal_factors WHERE created_at < :cutoff"
                ), {"cutoff": cutoff})
                counts["signal_factors"] = result.rowcount

                # Delete signal_outcomes for old signals
                result = sess.execute(text(
                    "DELETE FROM signal_outcomes WHERE reported_at < :cutoff"
                ), {"cutoff": cutoff})
                counts["signal_outcomes"] = result.rowcount

                # Delete messages older than cutoff
                result = sess.execute(text(
                    "DELETE FROM messages WHERE date < :cutoff"
                ), {"cutoff": cutoff})
                counts["messages"] = result.rowcount

                sess.commit()

            logger.info(
                "Data retention pass complete (cutoff=%s, retention_days=%d): %s",
                cutoff.isoformat(), self._retention_days, counts,
            )
        except Exception as e:
            logger.error("Data retention pass failed: %s", e, exc_info=True)
            counts["error"] = str(e)

        return counts

    def run_loop(self) -> None:
        """Background daemon loop — runs retention at interval."""
        # Run once immediately on startup
        self.run_once()

        while not self._shutdown.is_set():
            if self._shutdown.wait(timeout=self._interval_hours * 3600):
                break
            self.run_once()

    def signal_shutdown(self) -> None:
        self._shutdown.set()

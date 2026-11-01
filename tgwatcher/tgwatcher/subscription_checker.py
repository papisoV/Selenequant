"""Subscription expiry checker — marks expired subscriptions automatically.

Runs on startup and at a configurable interval. Sets status='expired' on
BotSubscription rows where expires_at < now() and status='active'.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from tgwatcher.models import BotSubscription

logger = logging.getLogger(__name__)


class SubscriptionChecker:
    """Periodically marks expired subscriptions.

    Thread-safe. Runs in a background daemon thread.
    """

    def __init__(self, storage, interval_hours: int = 1) -> None:
        self._storage = storage
        self._interval_hours = interval_hours
        self._shutdown = threading.Event()

    def run_once(self) -> int:
        """Execute one expiry check. Returns count of newly expired subscriptions."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        count = 0

        try:
            with self._storage.get_session() as sess:
                expired = sess.query(BotSubscription).filter(
                    BotSubscription.status == "active",
                    BotSubscription.expires_at != None,
                    BotSubscription.expires_at < now,
                ).all()

                for sub in expired:
                    sub.status = "expired"
                    count += 1

                if count:
                    sess.commit()
                    logger.info("SubscriptionChecker: expired %d subscriptions", count)
        except Exception as e:
            logger.error("SubscriptionChecker failed: %s", e, exc_info=True)

        return count

    def run_loop(self) -> None:
        """Background daemon loop — runs check at interval."""
        self.run_once()

        while not self._shutdown.is_set():
            if self._shutdown.wait(timeout=self._interval_hours * 3600):
                break
            self.run_once()

    def signal_shutdown(self) -> None:
        self._shutdown.set()

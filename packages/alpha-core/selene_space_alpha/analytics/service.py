"""Analytics service — manages provider instances and lifecycle.

Singleton-style service instantiated during API lifespan startup.
Each provider is created lazily on first compute and reused.
"""

from __future__ import annotations

import logging

from selene_space_alpha.analytics import AnalyticsProvider, AnalyticsProviderConfig
from selene_space_alpha.analytics.registry import get_provider

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Manages analytics provider instances for the API."""

    def __init__(self) -> None:
        self._providers: dict[str, AnalyticsProvider] = {}

    def get_provider(self, cfg: AnalyticsProviderConfig) -> AnalyticsProvider:
        """Get or create a provider instance by cfg.provider id."""
        pid = cfg.provider
        if pid not in self._providers:
            self._providers[pid] = get_provider(cfg)
        return self._providers[pid]

    async def start_all(self) -> None:
        """Start all created providers (called during lifespan startup)."""
        for p in self._providers.values():
            if not p.is_running:
                await p.start()

    async def stop_all(self) -> None:
        """Stop all providers (called during lifespan shutdown)."""
        for p in self._providers.values():
            if p.is_running:
                await p.stop()

    async def healthcheck_all(self) -> dict[str, bool]:
        """Run healthcheck on all providers."""
        result: dict[str, bool] = {}
        for pid, p in self._providers.items():
            try:
                if not p.is_running:
                    await p.start()
                result[pid] = await p.healthcheck()
            except Exception as exc:  # noqa: BLE001
                logger.warning("healthcheck %s failed: %s", pid, exc)
                result[pid] = False
        return result

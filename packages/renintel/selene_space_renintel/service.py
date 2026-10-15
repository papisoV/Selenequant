"""RenIntel service — manages provider instances and lifecycle.

Singleton-style service instantiated during API lifespan startup.
Each provider is created lazily on first compute and reused.
Observability: every compute call is wrapped with `track_compute` for
structured logging (provider/action/duration/success).
"""

from __future__ import annotations

import logging
from typing import Any

from selene_space_renintel.base import (
    RenIntelProvider,
    RenIntelProviderConfig,
    RenIntelReport,
)
from selene_space_renintel.observability import track_compute
from selene_space_renintel.registry import get_provider

logger = logging.getLogger(__name__)


class RenIntelService:
    """Manages Renaissance provider instances for the API."""

    def __init__(self) -> None:
        self._providers: dict[str, RenIntelProvider] = {}

    def get_provider(self, cfg: RenIntelProviderConfig) -> RenIntelProvider:
        """Get or create a provider instance by cfg.provider id."""
        pid = cfg.provider
        if pid not in self._providers:
            self._providers[pid] = get_provider(cfg)
        return self._providers[pid]

    async def compute(
        self,
        cfg: RenIntelProviderConfig,
        *,
        action: str,
        task: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> RenIntelReport:
        """Compute with observability tracking."""
        provider = self.get_provider(cfg)
        with track_compute(provider=cfg.provider, action=action) as ctx:
            report = await provider.compute(
                action=action,
                task=task,
                params=params,
            )
            ctx["success"] = True
            ctx["metrics_keys"] = list(report.metrics.keys())

        return report

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

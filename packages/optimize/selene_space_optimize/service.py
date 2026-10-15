"""Optimize service — manages provider instances and lifecycle.

Singleton-style service instantiated during API lifespan startup.
Each provider is created lazily on first compute and reused.
Observability: every compute call is wrapped with `track_compute` for
structured logging (provider/action/duration/success).
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from selene_space_optimize.base import (
    OptimizeProvider,
    OptimizeProviderConfig,
    OptimizeReport,
)
from selene_space_optimize.observability import track_compute
from selene_space_optimize.registry import get_provider

logger = logging.getLogger(__name__)


class OptimizeService:
    """Manages optimize provider instances for the API."""

    def __init__(self) -> None:
        self._providers: dict[str, OptimizeProvider] = {}

    def get_provider(self, cfg: OptimizeProviderConfig) -> OptimizeProvider:
        """Get or create a provider instance by cfg.provider id."""
        pid = cfg.provider
        if pid not in self._providers:
            self._providers[pid] = get_provider(cfg)
        return self._providers[pid]

    async def compute(
        self,
        cfg: OptimizeProviderConfig,
        *,
        action: str,
        prices: pd.DataFrame | pd.Series | None = None,
        returns: pd.DataFrame | pd.Series | None = None,
        weights: list[float] | None = None,
        benchmark: pd.DataFrame | pd.Series | None = None,
        params: dict[str, Any] | None = None,
    ) -> OptimizeReport:
        """Compute with observability tracking.

        Routes to the provider's compute method, wraps it with track_compute
        for structured logging. MLflow logging (if installed) is invoked
        when metrics contain numeric values.
        """
        provider = self.get_provider(cfg)
        with track_compute(provider=cfg.provider, action=action) as ctx:
            report = await provider.compute(
                action=action,
                prices=prices,
                returns=returns,
                weights=weights,
                benchmark=benchmark,
                params=params,
            )
            ctx["success"] = True
            ctx["metrics_keys"] = list(report.metrics.keys())

        # Best-effort MLflow logging — no-op if mlflow not installed
        from selene_space_optimize.observability import mlflow_log  # noqa: PLC0415

        mlflow_log(
            run_name=f"{cfg.provider}_{action}",
            metrics={
                k: float(v)
                for k, v in report.metrics.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            },
            params={
                "provider": cfg.provider,
                "action": action,
                **(params or {}),
            },
            tags={"kind": report.kind.value},
        )

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

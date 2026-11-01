"""FinRL provider — reinforcement learning trading (P2).

Wraps finrl (https://github.com/AI4Finance-Foundation/FinRL). Heavy dependency.
Batch 3 (selene_space_optimize) will provide the real RL training; this stub
exposes a thin interface so analytics API can route requests.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from selene_space_alpha.analytics.base import (
    AnalyticsProviderConfig,
    AnalyticsReport,
    AnalyticsReportKind,
)

logger = logging.getLogger(__name__)


class FinRLProvider:
    """Async FinRL analytics provider (P2 — placeholder)."""

    def __init__(self, cfg: AnalyticsProviderConfig) -> None:
        self._cfg = cfg
        self._running = False

    @property
    def config(self) -> AnalyticsProviderConfig:
        return self._cfg

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def healthcheck(self) -> bool:
        try:
            import finrl  # noqa: F401, PLC0415
            return True
        except ImportError as exc:
            logger.warning("finrl not installed: %s", exc)
            return False

    async def compute(
        self,
        *,
        action: str,
        returns: pd.DataFrame | pd.Series | None = None,
        prices: pd.DataFrame | pd.Series | None = None,
        weights: list[float] | None = None,
        benchmark: pd.DataFrame | pd.Series | None = None,
        params: dict[str, Any] | None = None,
    ) -> AnalyticsReport:
        if action not in {"train", "evaluate", "trade"}:
            raise KeyError(f"Unknown action: {action}")

        # P2 stub: defer real RL to batch 3 (selene_space_optimize).
        return AnalyticsReport(
            provider="finrl",
            kind=AnalyticsReportKind.OTHER,
            title=f"FinRL {action} (stub)",
            metrics={
                "action": action,
                "stub": True,
                "message": "Real RL training deferred to batch 3 (selene_space_optimize)",
            },
            computed_at=datetime.now(UTC),
            notes="P2 stub — integration deferred",
        )

    def list_actions(self) -> list[str]:
        return ["train", "evaluate", "trade"]

    def describe_action(self, action: str) -> str:
        return {
            "train": "Train RL agent (stub — deferred to batch 3)",
            "evaluate": "Evaluate RL agent (stub)",
            "trade": "Run RL trading (stub)",
        }.get(action, action)


def register() -> None:
    from selene_space_alpha.analytics.registry import register_provider

    register_provider("finrl", lambda cfg: FinRLProvider(cfg))


register()

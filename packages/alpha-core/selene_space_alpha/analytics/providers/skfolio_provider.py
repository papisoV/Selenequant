"""Skfolio provider — scikit-learn-style portfolio optimization.

Wraps skfolio (https://github.com/skfolio/skfolio).
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


class SkfolioProvider:
    """Async Skfolio analytics provider."""

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
            import skfolio  # noqa: F401, PLC0415
            return True
        except ImportError as exc:
            logger.warning("skfolio not installed: %s", exc)
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
        if action not in {"mean_risk", "min_volatility", "max_sharpe"}:
            raise KeyError(f"Unknown action: {action}")
        if prices is None and returns is None:
            raise ValueError("skfolio requires prices or returns")
        if prices is None:
            prices = (1 + returns).cumprod() if returns is not None else None
        assert prices is not None

        from skfolio.optimization import MeanRisk, Objective  # noqa: PLC0415
        from skfolio import RiskMeasure  # noqa: PLC0415

        objective_map = {
            "min_volatility": Objective.MINIMIZE_RISK,
            "max_sharpe": Objective.MAXIMIZE_RATIO,
            "mean_risk": Objective.MAXIMIZE_RETURN,
        }
        rm = RiskMeasure.VARIANCE
        opt = MeanRisk(
            objective_function=objective_map[action],
            risk_measure=rm,
            min_weights=0.0,
        )
        try:
            opt.fit(prices)
            weights_arr = opt.weights_
        except Exception as exc:  # noqa: BLE001
            logger.warning("skfolio fit failed: %s", exc)
            weights_arr = [1.0 / prices.shape[1]] * prices.shape[1]

        weights_dict = {
            str(col): round(float(w), 6)
            for col, w in zip(prices.columns, weights_arr, strict=False)
            if w > 1e-6
        }
        return AnalyticsReport(
            provider="skfolio",
            kind=AnalyticsReportKind.PORTFOLIO,
            title=f"Skfolio Optimization: {action}",
            metrics={"action": action, **weights_dict},
            data=pd.DataFrame(
                list(weights_dict.items()), columns=["asset", "weight"]
            ),
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["mean_risk", "min_volatility", "max_sharpe"]

    def describe_action(self, action: str) -> str:
        return {
            "mean_risk": "Mean-risk optimization (maximize return)",
            "min_volatility": "Minimum variance portfolio",
            "max_sharpe": "Maximum Sharpe ratio portfolio",
        }.get(action, action)


def register() -> None:
    from selene_space_alpha.analytics.registry import register_provider

    register_provider("skfolio", lambda cfg: SkfolioProvider(cfg))


register()

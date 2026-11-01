"""PyPortfolioOpt provider — portfolio optimization (mean-variance etc.).

Wraps pyportfolioopt (https://github.com/robertmartin8/PyPortfolioOpt).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from selene_space_alpha.analytics.base import (
    AnalyticsProviderConfig,
    AnalyticsReport,
    AnalyticsReportKind,
)

logger = logging.getLogger(__name__)


class PyPortfolioOptProvider:
    """Async PyPortfolioOpt analytics provider."""

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
            import pypfopt  # noqa: F401, PLC0415
            return True
        except ImportError as exc:
            logger.warning("pypfopt not installed: %s", exc)
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
        if action not in {"mean_variance", "min_volatility", "max_sharpe", "risk_parity"}:
            raise KeyError(f"Unknown action: {action}")
        if prices is None and returns is None:
            raise ValueError("pyportfolioopt requires prices or returns")
        if prices is None:
            # Synthesize prices from returns.
            prices = (1 + returns).cumprod() if returns is not None else None
        assert prices is not None

        from pypfopt import expected_returns, risk_models  # noqa: PLC0415
        from pypfopt.efficient_frontier import EfficientFrontier  # noqa: PLC0415
        from pypfopt import objective_prior  # noqa: PLC0415, F401

        mu = expected_returns.mean_historical_return(prices)
        S = risk_models.sample_cov(prices)
        ef = EfficientFrontier(mu, S)

        if action == "mean_variance":
            target_vol = float((params or {}).get("target_volatility", 0.20))
            try:
                ef.efficient_volatility(target_vol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("mean_variance failed: %s, fallback to min_vol", exc)
                ef.min_volatility()
        elif action == "min_volatility":
            ef.min_volatility()
        elif action == "max_sharpe":
            ef.max_sharpe()
        elif action == "risk_parity":
            try:
                ef.nonconvex_objective(
                    objective_function=lambda w, S: np.sum((w * (S @ w)) ** 2),
                    objective_args=(S,),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("risk_parity failed: %s, fallback to min_vol", exc)
                ef.min_volatility()

        cleaned = ef.clean_weights()
        weights_dict = {k: float(v) for k, v in cleaned.items() if v > 1e-6}
        try:
            perf = ef.portfolio_performance()
            perf_metrics = {
                "expected_return": round(float(perf[0]), 6),
                "annual_volatility": round(float(perf[1]), 6),
                "sharpe": round(float(perf[2]), 6),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("portfolio_performance failed: %s", exc)
            perf_metrics = {}

        return AnalyticsReport(
            provider="pyportfolioopt",
            kind=AnalyticsReportKind.PORTFOLIO,
            title=f"Optimization: {action}",
            metrics={**weights_dict, **perf_metrics, "action": action},
            data=pd.DataFrame(
                list(weights_dict.items()), columns=["asset", "weight"]
            ),
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["mean_variance", "min_volatility", "max_sharpe", "risk_parity"]

    def describe_action(self, action: str) -> str:
        return {
            "mean_variance": "Mean-variance optimization with target volatility",
            "min_volatility": "Minimum volatility portfolio",
            "max_sharpe": "Maximum Sharpe ratio portfolio",
            "risk_parity": "Risk parity (equal risk contribution)",
        }.get(action, action)


def register() -> None:
    from selene_space_alpha.analytics.registry import register_provider

    register_provider("pyportfolioopt", lambda cfg: PyPortfolioOptProvider(cfg))


register()

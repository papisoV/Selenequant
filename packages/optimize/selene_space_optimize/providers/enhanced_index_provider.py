"""Enhanced Index tracking provider (scipy-based).

Tracks a benchmark index with optional enhancements: factor tilt + sector
deviation control. Minimizes tracking error subject to constraints.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from selene_space_optimize.base import (
    OptimizeKind,
    OptimizeProviderConfig,
    OptimizeReport,
)

logger = logging.getLogger(__name__)


def _tracking_error(weights: np.ndarray, returns: np.ndarray, bench: np.ndarray) -> float:
    """Annualized tracking error vs benchmark."""
    port_ret = returns @ weights
    diff = port_ret - bench
    return float(np.std(diff) * np.sqrt(252))


class EnhancedIndexProvider:
    """Async enhanced-index tracking provider."""

    def __init__(self, cfg: OptimizeProviderConfig) -> None:
        self._cfg = cfg
        self._running = False

    @property
    def config(self) -> OptimizeProviderConfig:
        return self._cfg

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def healthcheck(self) -> bool:
        return True

    async def compute(
        self,
        *,
        action: str,
        prices: pd.DataFrame | pd.Series | None = None,
        returns: pd.DataFrame | pd.Series | None = None,
        weights: list[float] | None = None,
        benchmark: pd.DataFrame | pd.Series | None = None,
        params: dict[str, Any] | None = None,
    ) -> OptimizeReport:
        if action not in {"track", "info"}:
            raise KeyError(f"Unknown action: {action}")
        if returns is None and prices is None:
            raise ValueError("enhanced_index requires returns or prices")
        if benchmark is None:
            raise ValueError("enhanced_index requires benchmark series")

        if action == "info":
            return OptimizeReport(
                provider="enhanced_index",
                kind=OptimizeKind.OTHER,
                title="Enhanced Index Info",
                metrics={"method": "tracking_error_minimization", "long_only": True},
                computed_at=datetime.now(UTC),
            )

        if returns is None:
            assert prices is not None
            returns = prices.pct_change().dropna()
        if isinstance(returns, pd.Series):
            returns = returns.to_frame("asset")
        if isinstance(benchmark, pd.DataFrame):
            bench = benchmark.iloc[:, 0]
        else:
            bench = benchmark

        # Align index
        common = returns.index.intersection(bench.index)
        returns = returns.loc[common]
        bench = bench.loc[common]
        ret_arr = returns.to_numpy()
        bench_arr = bench.to_numpy()
        n = ret_arr.shape[1]
        params = params or {}
        max_dev = float(params.get("max_weight", 0.20))
        min_w = float(params.get("min_weight", 0.0))

        try:
            bounds = tuple((min_w, max_dev) for _ in range(n))
            constraints = ({"type": "eq", "fun": lambda w: np.sum(w) - 1.0},)
            x0 = np.full(n, 1.0 / n)
            res = minimize(
                _tracking_error, x0, args=(ret_arr, bench_arr),
                method="SLSQP", bounds=bounds, constraints=constraints,
                options={"maxiter": 500, "ftol": 1e-10},
            )
            if not res.success:
                raise RuntimeError(f"SLSQP failed: {res.message}")
            w = res.x
            te = _tracking_error(w, ret_arr, bench_arr)
            port_ret = ret_arr @ w
            port_annual = float(port_ret.mean() * 252)
            bench_annual = float(bench_arr.mean() * 252)
            excess = port_annual - bench_annual
            metrics = {
                "tracking_error": round(te, 6),
                "portfolio_return": round(port_annual, 6),
                "benchmark_return": round(bench_annual, 6),
                "excess_return": round(excess, 6),
                "information_ratio": round(excess / te, 6) if te > 0 else 0.0,
                "converged": bool(res.success),
            }
            data = pd.DataFrame(
                {"weight": w},
                index=list(returns.columns),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("enhanced_index failed: %s", exc)
            metrics = {"error": str(exc)}
            data = None

        return OptimizeReport(
            provider="enhanced_index",
            kind=OptimizeKind.PORTFOLIO,
            title="Enhanced Index Tracking",
            metrics=metrics,
            data=data,
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["track", "info"]

    def describe_action(self, action: str) -> str:
        return {
            "track": "Minimize tracking error vs benchmark (params: max_weight, min_weight)",
            "info": "Provider info",
        }.get(action, action)


def register() -> None:
    from selene_space_optimize.registry import register_provider

    register_provider("enhanced_index", lambda cfg: EnhancedIndexProvider(cfg))


register()

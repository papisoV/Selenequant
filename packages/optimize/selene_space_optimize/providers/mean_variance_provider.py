"""Mean-Variance optimization provider (scipy-based).

Implements 4 actions:
- max_sharpe: maximize (return - rf) / vol
- min_volatility: minimize portfolio std dev
- efficient_frontier: sample N points on the frontier
- target_return: efficient portfolio at target return
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


def _portfolio_stats(weights: np.ndarray, mean_returns: np.ndarray, cov: np.ndarray) -> tuple[float, float, float]:
    """Returns (annual_return, annual_vol, sharpe) assuming 252-day year."""
    annual_ret = float(np.sum(mean_returns * weights) * 252)
    annual_vol = float(np.sqrt(weights @ cov @ weights) * np.sqrt(252))
    sharpe = annual_ret / annual_vol if annual_vol > 0 else 0.0
    return annual_ret, annual_vol, sharpe


def _neg_sharpe(weights: np.ndarray, mean_returns: np.ndarray, cov: np.ndarray, rf: float = 0.0) -> float:
    ret, vol, _ = _portfolio_stats(weights, mean_returns, cov)
    return -(ret - rf) / vol if vol > 0 else 0.0


def _portfolio_vol(weights: np.ndarray, cov: np.ndarray) -> float:
    return float(np.sqrt(weights @ cov @ weights) * np.sqrt(252))


class MeanVarianceProvider:
    """Async mean-variance optimization provider."""

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
        return True  # scipy always available

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
        if action not in {"max_sharpe", "min_volatility", "efficient_frontier", "target_return"}:
            raise KeyError(f"Unknown action: {action}")
        if returns is None and prices is None:
            raise ValueError("mean_variance requires returns or prices")

        if returns is None:
            assert prices is not None
            returns = prices.pct_change().dropna()
        if isinstance(returns, pd.Series):
            returns = returns.to_frame("asset")
        if returns.shape[1] < 1:
            raise ValueError("returns has no columns")

        mean_returns = returns.mean().to_numpy()
        cov = returns.cov().to_numpy()
        n_assets = len(mean_returns)
        params = params or {}
        rf = float(params.get("risk_free_rate", 0.0))

        try:
            if action == "max_sharpe":
                result = self._opt_max_sharpe(mean_returns, cov, rf)
                metrics = self._metrics_dict(result, mean_returns, cov, rf)
                data = self._weights_df(result, returns.columns)
            elif action == "min_volatility":
                result = self._opt_min_vol(cov)
                metrics = self._metrics_dict(result, mean_returns, cov, rf)
                data = self._weights_df(result, returns.columns)
            elif action == "efficient_frontier":
                n_points = int(params.get("n_points", 30))
                frontier = self._efficient_frontier(mean_returns, cov, rf, n_points)
                metrics = {"n_points": len(frontier)}
                data = pd.DataFrame(frontier)
            else:  # target_return
                target = float(params.get("target", 0.10))
                result = self._opt_target_return(mean_returns, cov, target)
                metrics = self._metrics_dict(result, mean_returns, cov, rf)
                metrics["target_return"] = target
                data = self._weights_df(result, returns.columns)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mean_variance %s failed: %s", action, exc)
            metrics = {"error": str(exc)}
            data = None

        return OptimizeReport(
            provider="mean_variance",
            kind=OptimizeKind.PORTFOLIO,
            title=f"Mean-Variance: {action}",
            metrics=metrics,
            data=data,
            computed_at=datetime.now(UTC),
        )

    def _opt_max_sharpe(self, mean_returns: np.ndarray, cov: np.ndarray, rf: float) -> np.ndarray:
        n = len(mean_returns)
        bounds = tuple((0.0, 1.0) for _ in range(n))
        constraints = ({"type": "eq", "fun": lambda w: np.sum(w) - 1.0},)
        x0 = np.full(n, 1.0 / n)
        res = minimize(
            _neg_sharpe, x0, args=(mean_returns, cov, rf),
            method="SLSQP", bounds=bounds, constraints=constraints,
        )
        if not res.success:
            raise RuntimeError(f"SLSQP failed: {res.message}")
        return res.x

    def _opt_min_vol(self, cov: np.ndarray) -> np.ndarray:
        n = cov.shape[0]
        bounds = tuple((0.0, 1.0) for _ in range(n))
        constraints = ({"type": "eq", "fun": lambda w: np.sum(w) - 1.0},)
        x0 = np.full(n, 1.0 / n)
        res = minimize(
            _portfolio_vol, x0, args=(cov,),
            method="SLSQP", bounds=bounds, constraints=constraints,
        )
        if not res.success:
            raise RuntimeError(f"SLSQP failed: {res.message}")
        return res.x

    def _opt_target_return(self, mean_returns: np.ndarray, cov: np.ndarray, target: float) -> np.ndarray:
        n = len(mean_returns)
        bounds = tuple((0.0, 1.0) for _ in range(n))
        constraints = (
            {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
            {"type": "eq", "fun": lambda w: np.sum(mean_returns * w) * 252 - target},
        )
        x0 = np.full(n, 1.0 / n)
        res = minimize(
            _portfolio_vol, x0, args=(cov,),
            method="SLSQP", bounds=bounds, constraints=constraints,
        )
        if not res.success:
            raise RuntimeError(f"SLSQP failed: {res.message}")
        return res.x

    def _efficient_frontier(self, mean_returns: np.ndarray, cov: np.ndarray, rf: float, n_points: int) -> list[dict[str, float]]:
        rets = mean_returns * 252
        min_r, max_r = float(rets.min()), float(rets.max())
        targets = np.linspace(min_r, max_r, n_points)
        out: list[dict[str, float]] = []
        for t in targets:
            try:
                w = self._opt_target_return(mean_returns, cov, float(t))
                r, v, s = _portfolio_stats(w, mean_returns, cov)
                out.append({"return": round(r, 6), "volatility": round(v, 6), "sharpe": round(s, 6)})
            except Exception as exc:  # noqa: BLE001
                logger.debug("frontier point target=%s failed: %s", t, exc)
        return out

    def _metrics_dict(self, weights: np.ndarray, mean_returns: np.ndarray, cov: np.ndarray, rf: float) -> dict[str, float]:
        r, v, s = _portfolio_stats(weights, mean_returns, cov)
        return {
            "annual_return": round(r, 6),
            "annual_volatility": round(v, 6),
            "sharpe": round(s, 6),
            "risk_free_rate": rf,
        }

    def _weights_df(self, weights: np.ndarray, columns: pd.Index) -> pd.DataFrame:
        return pd.DataFrame(
            {"weight": weights},
            index=list(columns),
        )

    def list_actions(self) -> list[str]:
        return ["max_sharpe", "min_volatility", "efficient_frontier", "target_return"]

    def describe_action(self, action: str) -> str:
        return {
            "max_sharpe": "Maximize Sharpe ratio (long-only)",
            "min_volatility": "Minimize portfolio volatility (long-only)",
            "efficient_frontier": "Sample N points on the efficient frontier",
            "target_return": "Efficient portfolio at target return (param: target=0.10)",
        }.get(action, action)


def register() -> None:
    from selene_space_optimize.registry import register_provider

    register_provider("mean_variance", lambda cfg: MeanVarianceProvider(cfg))


register()

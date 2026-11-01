"""Risk Parity optimization provider (scipy-based).

Implements equal-risk-contribution portfolio: each asset contributes the same
amount of risk (volatility contribution). Uses convex optimization on the
inverse-variance proxy, refined with SLSQP.
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


def _risk_contributions(weights: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Marginal risk contribution per asset."""
    port_vol = float(np.sqrt(weights @ cov @ weights))
    if port_vol <= 0:
        return np.zeros_like(weights)
    marginal = cov @ weights / port_vol
    return weights * marginal


def _risk_parity_objective(weights: np.ndarray, cov: np.ndarray) -> float:
    """Sum of squared deviations from equal risk contribution."""
    rc = _risk_contributions(weights, cov)
    target = rc.mean()
    return float(np.sum((rc - target) ** 2))


class RiskParityProvider:
    """Async risk-parity optimization provider."""

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
        if action not in {"equal_risk", "info"}:
            raise KeyError(f"Unknown action: {action}")
        if returns is None and prices is None:
            raise ValueError("risk_parity requires returns or prices")

        if action == "info":
            return OptimizeReport(
                provider="risk_parity",
                kind=OptimizeKind.OTHER,
                title="Risk Parity Info",
                metrics={"method": "equal_risk_contribution", "long_only": True},
                computed_at=datetime.now(UTC),
            )

        if returns is None:
            assert prices is not None
            returns = prices.pct_change().dropna()
        if isinstance(returns, pd.Series):
            returns = returns.to_frame("asset")

        cov = returns.cov().to_numpy() * 252  # annualized
        n = cov.shape[0]

        try:
            # Init: inverse-variance weights
            ivp = 1.0 / np.diag(cov)
            x0 = ivp / ivp.sum()
            bounds = tuple((0.0, 1.0) for _ in range(n))
            constraints = ({"type": "eq", "fun": lambda w: np.sum(w) - 1.0},)
            res = minimize(
                _risk_parity_objective, x0, args=(cov,),
                method="SLSQP", bounds=bounds, constraints=constraints,
                options={"maxiter": 500, "ftol": 1e-10},
            )
            if not res.success:
                raise RuntimeError(f"SLSQP failed: {res.message}")
            w = res.x
            rc = _risk_contributions(w, cov)
            port_vol = float(np.sqrt(w @ cov @ w))
            metrics = {
                "annual_volatility": round(port_vol, 6),
                "max_weight": round(float(w.max()), 6),
                "min_weight": round(float(w.min()), 6),
                "risk_concentration": round(float(np.std(rc) / (rc.mean() if rc.mean() > 0 else 1.0)), 6),
                "converged": bool(res.success),
            }
            data = pd.DataFrame(
                {"weight": w, "risk_contribution": rc},
                index=list(returns.columns),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("risk_parity failed: %s", exc)
            metrics = {"error": str(exc)}
            data = None

        return OptimizeReport(
            provider="risk_parity",
            kind=OptimizeKind.PORTFOLIO,
            title="Risk Parity (Equal Risk Contribution)",
            metrics=metrics,
            data=data,
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["equal_risk", "info"]

    def describe_action(self, action: str) -> str:
        return {
            "equal_risk": "Equal risk contribution portfolio (long-only)",
            "info": "Provider info",
        }.get(action, action)


def register() -> None:
    from selene_space_optimize.registry import register_provider

    register_provider("risk_parity", lambda cfg: RiskParityProvider(cfg))


register()

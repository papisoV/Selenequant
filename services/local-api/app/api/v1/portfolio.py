"""Portfolio recommendation API endpoint.

Exposes the Black-Litterman-based PortfolioRecommender (M4) as a POST
endpoint. Callers supply assets, market weights (optional, defaults to
equal weight), signals (strategy views), and optional covariance /
config. Returns target weights, BL posterior diagnostics, and
optimizer result.

Covariance lookup: when ``cov`` is omitted, the endpoint attempts to
pull recent close prices from the in-memory kline buffer via the
market service and computes a sample covariance. Falls back to
identity if no data is available.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.dependencies import build_response_meta, get_strategy_runner
from selene_space_types.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("")
async def get_portfolio_snapshot(request: Request) -> APIResponse[list[dict[str, Any]]]:
    """Return current portfolio snapshot.

    Reads from the StrategyRunner's paper trading state when available.
    Returns an empty list when the engine is not yet running or when
    no positions have been opened.
    """
    items: list[dict[str, Any]] = []

    try:
        runner = get_strategy_runner()
        if runner is not None:
            # StrategyRunner exposes slot status / paper state
            for slot in getattr(runner, "slots", []) or []:
                positions = (
                    slot.get("positions") if isinstance(slot, dict) else None
                ) or []
                for pos in positions:
                    items.append(
                        {
                            "deployment_id": slot.get("deployment_id")
                            if isinstance(slot, dict)
                            else None,
                            "symbol": pos.get("symbol") if isinstance(pos, dict) else None,
                            "side": pos.get("side") if isinstance(pos, dict) else None,
                            "size": pos.get("size") if isinstance(pos, dict) else None,
                            "entry_price": pos.get("entry_price")
                            if isinstance(pos, dict)
                            else None,
                            "current_price": pos.get("current_price")
                            if isinstance(pos, dict)
                            else None,
                            "unrealized_pnl": pos.get("unrealized_pnl")
                            if isinstance(pos, dict)
                            else None,
                        }
                    )
    except Exception:
        items = []

    return APIResponse(
        data=items,
        meta=build_response_meta(request),
        error=None,
    )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PortfolioRecommendRequest(BaseModel):
    """Request body for ``POST /api/v1/portfolio/recommend``.

    Either supply ``cov`` directly, or omit it and the endpoint will
    attempt to estimate from market data.
    """

    assets: list[str] = Field(..., min_length=1, description="Ordered asset symbols")
    market_weights: list[float] | None = Field(
        None,
        description="Market-cap weights (must sum to 1.0). Defaults to equal weight.",
    )
    cov: list[list[float]] | None = Field(
        None,
        description="Annualized covariance matrix (n x n). Estimated if omitted.",
    )
    signals: dict[str, float] = Field(
        default_factory=dict,
        description="Strategy views: {symbol: expected_excess_return}",
    )
    objective: str = Field(
        "max_sharpe",
        description="Optimizer objective: max_sharpe / mean_variance / min_variance / risk_parity / max_diversification",
    )
    tau: float = Field(0.05, ge=0.0, le=1.0, description="BL prior uncertainty scalar")
    risk_aversion: float = Field(1.0, gt=0.0, description="λ for mean_variance objective")
    weight_cap: float | None = Field(None, ge=0.0, le=1.0, description="Per-asset weight ceiling")
    turnover_limit: float | None = Field(None, ge=0.0, description="Max L1 turnover vs prev_weights")
    prev_weights: list[float] | None = Field(None, description="Current portfolio weights for turnover constraint")
    long_only: bool = Field(True, description="If True, weights ∈ [0, 1]")
    full_invested: bool = Field(True, description="If True, weights sum to 1.0 (alias for full_investment)")


class PortfolioRecommendResponse(BaseModel):
    """Response body for the recommendation endpoint."""

    assets: list[str]
    weights: list[float]
    objective: str
    success: bool
    message: str
    bl_mu_posterior: list[float]
    bl_mu_equilibrium: list[float]
    n_views: int
    turnover: float
    config: dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _estimate_cov_from_market(assets: list[str]) -> np.ndarray | None:
    """Try to estimate a covariance matrix from market_svc kline buffer.

    Returns None if any asset has insufficient data. Falls back to a
    simple identity scaled matrix in the caller.
    """
    try:
        from app.core.dependencies import get_market_service

        svc = get_market_service()
        if svc is None:
            return None

        closes_per_asset: list[list[float]] = []
        min_len = 999999
        for sym in assets:
            candidates = [sym, sym.replace("/", "-"), sym.replace("/", "")]
            found = None
            for _sym in candidates:
                for _ex, _mt in getattr(svc, "sources", {}).keys() or [
                    ("binance", "spot")
                ]:
                    klines = svc.get_klines(_sym, exchange=_ex, market_type=_mt, limit=60)
                    if klines:
                        found = [float(k.close) for k in klines]
                        break
                if found:
                    break
            if not found or len(found) < 5:
                return None
            closes_per_asset.append(found)
            min_len = min(min_len, len(found))

        # Trim all series to the same length (most recent window).
        returns_matrix = []
        for closes in closes_per_asset:
            trimmed = closes[-min_len:]
            rets = [
                (trimmed[i] - trimmed[i - 1]) / trimmed[i - 1]
                for i in range(1, len(trimmed))
                if trimmed[i - 1] > 0
            ]
            returns_matrix.append(rets)

        if not returns_matrix or len(returns_matrix[0]) < 2:
            return None

        # Annualized sample covariance (252 periods/year assumption).
        arr = np.array(returns_matrix)  # shape (n_assets, n_periods)
        # Sample cov: rows are assets, columns are periods → use rowvar=False.
        cov = np.cov(arr, rowvar=True) * 252
        # Ensure PSD (numerical safety).
        cov = (cov + cov.T) / 2.0
        return cov
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/recommend", response_model=PortfolioRecommendResponse)
def recommend_portfolio(req: PortfolioRecommendRequest) -> PortfolioRecommendResponse:
    """Run Black-Litterman → optimizer → target weights."""
    from selene_space_alpha.portfolio import (
        PortfolioRecommender,
        RecommenderConfig,
    )

    n = len(req.assets)

    # Market weights default to equal weight.
    if req.market_weights is None:
        mw = np.ones(n) / n
    else:
        if len(req.market_weights) != n:
            raise HTTPException(
                status_code=400,
                detail=f"market_weights length {len(req.market_weights)} != assets length {n}",
            )
        mw = np.array(req.market_weights, dtype=float)
        if not np.isclose(np.sum(mw), 1.0, atol=1e-6):
            raise HTTPException(
                status_code=400,
                detail=f"market_weights must sum to 1.0 (got {float(np.sum(mw))})",
            )

    # Covariance: supplied or estimated.
    if req.cov is None:
        cov = _estimate_cov_from_market(req.assets)
        if cov is None:
            # Fallback: identity scaled to 0.04 (20% annual vol) per asset.
            cov = np.eye(n) * 0.04
    else:
        if len(req.cov) != n or any(len(row) != n for row in req.cov):
            raise HTTPException(
                status_code=400,
                detail=f"cov must be {n}x{n}",
            )
        cov = np.array(req.cov, dtype=float)

    # prev_weights validation.
    prev_w = None
    if req.prev_weights is not None:
        if len(req.prev_weights) != n:
            raise HTTPException(
                status_code=400,
                detail=f"prev_weights length {len(req.prev_weights)} != assets length {n}",
            )
        prev_w = np.array(req.prev_weights, dtype=float)

    # Validate objective.
    valid_objectives = {
        "max_sharpe", "mean_variance", "min_variance",
        "risk_parity", "max_diversification",
    }
    if req.objective not in valid_objectives:
        raise HTTPException(
            status_code=400,
            detail=f"objective must be one of {sorted(valid_objectives)}",
        )

    config = RecommenderConfig(
        tau=req.tau,
        objective=req.objective,
        long_only=req.long_only,
        full_investment=req.full_invested,
        weight_cap=req.weight_cap,
        turnover_limit=req.turnover_limit,
    )
    recommender = PortfolioRecommender(config=config)

    try:
        rec = recommender.recommend(
            assets=req.assets,
            market_weights=mw,
            cov=cov,
            signals=req.signals,
            prev_weights=prev_w,
            risk_aversion=req.risk_aversion,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("optimizer failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    return PortfolioRecommendResponse(
        assets=rec.assets,
        weights=rec.weights.tolist(),
        objective=rec.opt_result.objective,
        success=bool(rec.opt_result.success),
        message=rec.opt_result.message,
        bl_mu_posterior=rec.bl_result.mu_posterior.tolist(),
        bl_mu_equilibrium=rec.bl_result.mu_equilibrium.tolist(),
        n_views=int(rec.bl_result.views_P.shape[0]),
        turnover=rec.turnover,
        config={
            "tau": config.tau,
            "objective": config.objective,
            "long_only": config.long_only,
            "full_investment": config.full_investment,
            "weight_cap": config.weight_cap,
            "turnover_limit": config.turnover_limit,
        },
    )

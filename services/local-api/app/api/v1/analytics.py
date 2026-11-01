"""Analytics endpoints.

Exposes 8 providers (quantstats, pyportfolioopt, skfolio, statsmodels, ffn,
tsmoothie, gluonts, finrl) for portfolio performance, risk, forecasting,
smoothing, and statistics. Inspired by a commercial analytics terminal's
analytics module (clean-room reimplementation, no source code copied).
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.core.dependencies import get_analytics_service
from selene_space_alpha.analytics import AnalyticsProviderConfig
from selene_space_alpha.analytics.registry import list_registered

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


class ProviderInfo(BaseModel):
    provider: str
    is_running: bool
    healthy: bool | None = None


class ActionInfo(BaseModel):
    action: str
    description: str


class SeriesPayload(BaseModel):
    """Column-oriented series payload: {index: [...], columns: {name: [values]}}.

    `index` is optional (defaults to integer position). `columns` maps column
    name → list of numeric values. For a single series, use one column.
    """

    index: list[str] | None = None
    columns: dict[str, list[float]]


class AnalyticsRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    action: str
    returns: SeriesPayload | None = None
    prices: SeriesPayload | None = None
    weights: list[float] | None = None
    benchmark: SeriesPayload | None = None
    params: dict[str, Any] | None = None


class AnalyticsResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    kind: str
    title: str
    metrics: dict[str, Any]
    data: dict[str, Any] | None = None
    artifacts: dict[str, Any] | None = None
    computed_at: str
    notes: str | None = None


def _df_from_payload(p: SeriesPayload | None) -> pd.DataFrame | None:
    if p is None:
        return None
    df = pd.DataFrame(p.columns)
    if p.index is not None:
        try:
            df.index = pd.to_datetime(p.index)
        except (ValueError, TypeError):
            df.index = p.index
    return df


def _series_from_payload(p: SeriesPayload | None) -> pd.Series | pd.DataFrame | None:
    if p is None:
        return None
    df = _df_from_payload(p)
    if df is None:
        return None
    if len(df.columns) == 1:
        return df.iloc[:, 0]
    return df


def _df_to_dict(df: pd.DataFrame | pd.Series | None) -> dict[str, Any] | None:
    if df is None:
        return None
    try:
        if isinstance(df, pd.Series):
            df = df.to_frame("value")
        obj = df.to_dict(orient="split")
        return {
            "columns": list(obj.get("columns", [])),
            "index": [str(i) for i in obj.get("index", [])],
            "data": [list(row) for row in obj.get("data", [])],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("df_to_dict failed: %s", exc)
        return None


def _get_provider(provider: str) -> Any:
    cfg = AnalyticsProviderConfig(provider=provider)
    service = get_analytics_service()
    if service is None:
        raise HTTPException(status_code=503, detail="analytics service not initialized")
    return service.get_provider(cfg)


@router.get("/providers", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    """List all registered analytics providers."""
    out: list[ProviderInfo] = []
    for name in list_registered():
        try:
            p = _get_provider(name)
            healthy = None
            try:
                if p.is_running:
                    healthy = await p.healthcheck()
            except Exception as exc:  # noqa: BLE001
                logger.warning("healthcheck %s failed: %s", name, exc)
            out.append(ProviderInfo(provider=name, is_running=p.is_running, healthy=healthy))
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("provider %s lookup failed: %s", name, exc)
            out.append(ProviderInfo(provider=name, is_running=False, healthy=False))
    return out


@router.get("/{provider}/actions", response_model=list[ActionInfo])
async def list_actions(provider: str) -> list[ActionInfo]:
    """List a provider's available actions."""
    p = _get_provider(provider)
    return [ActionInfo(action=a, description=p.describe_action(a)) for a in p.list_actions()]


@router.get("/{provider}/healthcheck")
async def healthcheck(provider: str) -> dict[str, Any]:
    """Provider health check."""
    p = _get_provider(provider)
    if not p.is_running:
        await p.start()
    ok = await p.healthcheck()
    return {"provider": provider, "healthy": ok, "is_running": p.is_running}


@router.post("/report", response_model=AnalyticsResponse)
async def compute_report(req: AnalyticsRequest) -> AnalyticsResponse:
    """Compute an analytics report.

    Accepts returns/prices/benchmark as {column: [values...]} dicts plus
    optional weights/params. Returns metrics + data + artifacts.
    """
    p = _get_provider(req.provider)
    if not p.is_running:
        await p.start()
    try:
        returns = _series_from_payload(req.returns)
        prices = _series_from_payload(req.prices)
        benchmark = _series_from_payload(req.benchmark)
        report = await p.compute(
            action=req.action,
            returns=returns,
            prices=prices,
            weights=req.weights,
            benchmark=benchmark,
            params=req.params,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("analytics %s/%s failed: %s", req.provider, req.action, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    return AnalyticsResponse(
        provider=report.provider,
        kind=report.kind.value,
        title=report.title,
        metrics=report.metrics,
        data=_df_to_dict(report.data),
        artifacts=report.artifacts,
        computed_at=report.computed_at.isoformat(),
        notes=report.notes,
    )


@router.get("/portfolio/metrics", response_model=AnalyticsResponse)
async def portfolio_metrics(
    provider: str = "quantstats",
    action: str = "stats",
    weights: str | None = None,
    risk_free_rate: float = 0.0,
) -> AnalyticsResponse:
    """Convenience endpoint: portfolio-level performance metrics.

    Reads cached returns from the returns service (if available) and runs the
    specified provider/action. Defaults to quantstats/stats.
    """
    p = _get_provider(provider)
    if not p.is_running:
        await p.start()

    # Try to fetch returns from the backtest/returns cache (best-effort).
    returns: pd.Series | None = None
    try:
        from app.core.dependencies import get_returns_repo  # type: ignore

        repo = get_returns_repo()
        if repo is not None:
            rows = await repo.list(limit=1000)
            if rows:
                df = pd.DataFrame(rows)
                if "date" in df.columns and "return" in df.columns:
                    returns = pd.Series(df["return"].to_numpy(), index=pd.to_datetime(df["date"]))
    except Exception as exc:  # noqa: BLE001
        logger.info("portfolio metrics: returns cache unavailable: %s", exc)

    if returns is None:
        raise HTTPException(
            status_code=400,
            detail="no returns available — POST /analytics/report with returns instead",
        )

    w_list: list[float] | None = None
    if weights:
        try:
            w_list = [float(x) for x in weights.split(",")]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid weights: {exc}") from exc

    try:
        report = await p.compute(
            action=action,
            returns=returns,
            weights=w_list,
            params={"risk_free_rate": risk_free_rate},
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("portfolio metrics failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    return AnalyticsResponse(
        provider=report.provider,
        kind=report.kind.value,
        title=report.title,
        metrics=report.metrics,
        data=_df_to_dict(report.data),
        artifacts=report.artifacts,
        computed_at=report.computed_at.isoformat(),
        notes=report.notes,
    )

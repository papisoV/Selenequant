"""Macro data endpoints.

Provides curated macro economic series (FRED, BIS, BLS) for crypto quant research.
Each provider has healthcheck + retry + cached fallback (AC-Q4 compliant).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.dependencies import get_macrodata_service
from selene_space_macrodata import MacroDataSource, MacroDataSourceConfig
from selene_space_macrodata.registry import list_registered

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/macrodata", tags=["macrodata"])


class ProviderInfo(BaseModel):
    provider: str
    series_count: int
    is_running: bool


class SeriesCatalogItem(BaseModel):
    series_id: str
    kind: str
    description: str


class SeriesObservation(BaseModel):
    date: str
    value: float | None


class SeriesResponse(BaseModel):
    provider: str
    series_id: str
    kind: str
    description: str
    observations: list[SeriesObservation]
    fetched_at: str
    source_url: str | None = None
    from_cache: bool = False
    stale: bool = False


def _get_provider(provider: str) -> MacroDataSource:
    cfg = MacroDataSourceConfig(provider=provider)
    service = get_macrodata_service()
    return service.get_provider(cfg)


@router.get("/providers", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    """List all registered macro data providers."""
    service = get_macrodata_service()
    return [
        ProviderInfo(
            provider=p,
            series_count=len(service.get_provider(MacroDataSourceConfig(provider=p)).list_series()),
            is_running=service.get_provider(MacroDataSourceConfig(provider=p)).is_running,
        )
        for p in list_registered()
    ]


@router.get("/{provider}/series", response_model=list[SeriesCatalogItem])
async def list_series(provider: str) -> list[SeriesCatalogItem]:
    """List a provider's series catalog (with kind + description from catalog, not cache)."""
    # Read catalog metadata directly from provider module constants — cache may be empty.
    catalog_meta: dict[str, tuple[str, str]] = {}
    if provider == "fred":
        from selene_space_macrodata.providers.fred import FRED_CATALOG
        catalog_meta = {sid: (kind.value, desc) for sid, (kind, desc) in FRED_CATALOG.items()}
    elif provider == "bis":
        from selene_space_macrodata.providers.bis import BIS_CATALOG
        catalog_meta = {sid: (kind.value, desc) for sid, (kind, desc, _, _) in BIS_CATALOG.items()}
    elif provider == "bls":
        from selene_space_macrodata.providers.bls import BLS_CATALOG
        catalog_meta = {sid: (kind.value, desc) for sid, (kind, desc) in BLS_CATALOG.items()}

    p = _get_provider(provider)
    catalog: list[SeriesCatalogItem] = []
    for sid in p.list_series():
        kind, desc = catalog_meta.get(sid, ("other", sid))
        catalog.append(SeriesCatalogItem(series_id=sid, kind=kind, description=desc))
    return catalog


@router.get("/{provider}/series/{series_id}", response_model=SeriesResponse)
async def fetch_series(
    provider: str,
    series_id: str,
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
    limit: int | None = Query(None, ge=1, le=100000),
) -> SeriesResponse:
    """Fetch a macro series by provider + series_id, with cached fallback."""
    from datetime import datetime

    p = _get_provider(provider)
    if not p.is_running:
        await p.start()
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d") if start else None
        end_dt = datetime.strptime(end, "%Y-%m-%d") if end else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid date: {exc}") from exc

    cached = p.get_cached(series_id)
    try:
        series = await p.fetch_series(series_id, start=start_dt, end=end_dt, limit=limit)
        from_cache = cached is not None and cached.fetched_at == series.fetched_at
        return SeriesResponse(
            provider=series.provider,
            series_id=series.series_id,
            kind=series.kind.value,
            description=series.description,
            observations=[
                SeriesObservation(date=str(o.get("date", "")), value=o.get("value"))
                for o in series.observations.to_dict(orient="records")
            ],
            fetched_at=series.fetched_at.isoformat(),
            source_url=series.source_url,
            from_cache=from_cache,
            stale=False,
        )
    except Exception as exc:
        logger.error("macro fetch %s/%s failed: %s", provider, series_id, exc)
        if cached is not None:
            return SeriesResponse(
                provider=cached.provider,
                series_id=cached.series_id,
                kind=cached.kind.value,
                description=cached.description,
                observations=[
                    SeriesObservation(date=str(o.get("date", "")), value=o.get("value"))
                    for o in cached.observations.to_dict(orient="records")
                ],
                fetched_at=cached.fetched_at.isoformat(),
                source_url=cached.source_url,
                from_cache=True,
                stale=True,
            )
        raise HTTPException(status_code=502, detail="Failed to fetch macro data") from exc


@router.get("/{provider}/healthcheck")
async def healthcheck(provider: str) -> dict[str, Any]:
    """Provider health check."""
    p = _get_provider(provider)
    if not p.is_running:
        await p.start()
    ok = await p.healthcheck()
    return {"provider": provider, "healthy": ok, "is_running": p.is_running}

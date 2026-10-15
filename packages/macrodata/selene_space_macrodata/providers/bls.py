"""BLS provider — U.S. Bureau of Labor Statistics Public API v2.

Translated from <local-path-redacted>
P0 stub: implements Protocol, healthcheck + fetch_series with retry/fallback.
Catalog limited to crypto-relevant series (CPI, PPI, nonfarm, JOLTS).

Requires BLS_API_KEY env var (free: https://data.bls.gov/registrationEngine/).
Without key, public rate limit is 25 requests/day (still usable for healthcheck).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx
import pandas as pd

from selene_space_macrodata.fallback import (
    is_cache_valid,
    stale_fallback,
    with_retry,
)
from selene_space_macrodata.source import (
    MacroDataSourceConfig,
    MacroSeries,
    SeriesKind,
)

logger = logging.getLogger(__name__)

BLS_BASE_URL = "https://api.bls.gov/publicAPI/v2"

# Curated BLS series catalog (crypto-relevant).
BLS_CATALOG: dict[str, tuple[SeriesKind, str]] = {
    # CPI
    "CUUR0000SA0": (SeriesKind.INFLATION, "CPI All Urban Consumers All Items"),
    "CUUR0000SA0L1E": (SeriesKind.INFLATION, "CPI All Items Less Food & Energy (Core)"),
    # PPI
    "WPSFD49207": (SeriesKind.INFLATION, "PPI Final Demand"),
    # Employment
    "LNS14000000": (SeriesKind.EMPLOYMENT, "Unemployment Rate U-3"),
    "CES0000000001": (SeriesKind.EMPLOYMENT, "Total Nonfarm Employment"),
    "JTS000000000000000JOL": (SeriesKind.EMPLOYMENT, "Job Openings Total Nonfarm (JOLTS)"),
    # Earnings
    "CES0500000008": (SeriesKind.EMPLOYMENT, "Average Hourly Earnings Production"),
}


class BlsProvider:
    """Async BLS macro data provider.

    Implements MacroDataSource Protocol. BLS v2 API supports both GET (single series)
    and POST (multi-series). Uses POST for batch efficiency.
    """

    def __init__(self, cfg: MacroDataSourceConfig) -> None:
        self._cfg = cfg
        self._api_key = cfg.api_key or os.environ.get("BLS_API_KEY")
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, MacroSeries] = {}

    @property
    def config(self) -> MacroDataSourceConfig:
        return self._cfg

    @property
    def is_running(self) -> bool:
        return self._client is not None

    async def start(self) -> None:
        if self._client is None:
            proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
            self._client = httpx.AsyncClient(
                base_url=self._cfg.base_url or BLS_BASE_URL,
                timeout=httpx.Timeout(connect=5.0, read=self._cfg.timeout_seconds, write=10.0, pool=10.0),
                proxy=proxy,
            )

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def healthcheck(self) -> bool:
        if self._client is None:
            return False
        try:
            r = await self._client.get("/timeseries/data/CUUR0000SA0")
            return r.status_code == 200
        except Exception as exc:
            logger.warning("BLS healthcheck failed: %s", exc)
            return False

    async def fetch_series(
        self,
        series_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> MacroSeries:
        if series_id not in BLS_CATALOG:
            raise KeyError(f"Unknown BLS series: {series_id}")
        if self._client is None:
            await self.start()
        assert self._client is not None

        kind, desc = BLS_CATALOG[series_id]

        async def _do_fetch() -> MacroSeries:
            assert self._client is not None  # for mypy: started above
            params: dict[str, Any] = {}
            if self._api_key:
                params["registrationkey"] = self._api_key
            if start:
                params["startyear"] = str(start.year)
            if end:
                params["endyear"] = str(end.year)
            r = await self._client.get(f"/timeseries/data/{series_id}", params=params)
            r.raise_for_status()
            payload = r.json()
            obs = _extract_observations(payload, series_id)
            df = pd.DataFrame(obs)
            series = MacroSeries(
                provider="bls",
                series_id=series_id,
                kind=kind,
                description=desc,
                observations=df,
                fetched_at=datetime.now(UTC),
                source_url=f"{BLS_BASE_URL}/timeseries/data/{series_id}",
            )
            self._cache[series_id] = series
            return series

        try:
            return await with_retry(_do_fetch)
        except Exception as exc:
            logger.error("BLS fetch %s failed: %s", series_id, exc)
            cached = self._cache.get(series_id)
            if cached is not None and is_cache_valid(cached, self._cfg.cache_ttl_seconds):
                return cached
            return stale_fallback(cached, series_id)

    def get_cached(self, series_id: str) -> MacroSeries | None:
        cached = self._cache.get(series_id)
        if cached is None:
            return None
        return cached if is_cache_valid(cached, self._cfg.cache_ttl_seconds) else None

    def list_series(self) -> list[str]:
        return list(BLS_CATALOG.keys())


def _extract_observations(payload: dict[str, Any], series_id: str) -> list[dict[str, Any]]:
    """Extract observations from BLS v2 JSON payload.

    BLS v2 structure: Results.series[0].data = [{year, period, value, ...}, ...]
    """
    results = payload.get("Results", {})
    series_list = results.get("series", [])
    if not series_list:
        return []
    data = series_list[0].get("data", [])
    rows: list[dict[str, Any]] = []
    for item in data:
        if item.get("seriesID") and item["seriesID"] != series_id:
            continue
        value = item.get("value")
        if value in ("", None):
            continue
        year = item.get("year")
        period = item.get("period", "")
        # period format: "M01" for January, "Q01" for Q1, "A01" for annual
        if not year or not period:
            continue
        try:
            if period.startswith("M"):
                month = int(period[1:])
                date = pd.to_datetime(f"{year}-{month:02d}-01")
            elif period.startswith("Q"):
                quarter = int(period[1:])
                month = (quarter - 1) * 3 + 1
                date = pd.to_datetime(f"{year}-{month:02d}-01")
            elif period.startswith("A"):
                date = pd.to_datetime(f"{year}-01-01")
            else:
                continue
            rows.append({"date": date, "value": float(value)})
        except (ValueError, TypeError):
            continue
    return rows


def register() -> None:
    from selene_space_macrodata.registry import register_provider

    register_provider("bls", lambda cfg: BlsProvider(cfg))


register()

"""FRED provider — Federal Reserve Economic Data.

Translated from <local-path-redacted> + fred_economic_data.py.
Async, httpx-based, with healthcheck + retry + cached fallback.

Requires FRED_API_KEY env var (free: https://fredaccount.stlouisfed.org/apikeys).
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

FRED_BASE_URL = "https://api.stlouisfed.org/fred"

# Curated series catalog grouped by kind (translated from fred_economic_data.py).
# These are the crypto-relevant macro series.
FRED_CATALOG: dict[str, tuple[SeriesKind, str]] = {
    # Yield curve (rates)
    "DGS1MO": (SeriesKind.RATES, "1-Month Treasury constant maturity rate"),
    "DGS3MO": (SeriesKind.RATES, "3-Month Treasury constant maturity rate"),
    "DGS1": (SeriesKind.RATES, "1-Year Treasury constant maturity rate"),
    "DGS2": (SeriesKind.RATES, "2-Year Treasury constant maturity rate"),
    "DGS5": (SeriesKind.RATES, "5-Year Treasury constant maturity rate"),
    "DGS10": (SeriesKind.RATES, "10-Year Treasury constant maturity rate"),
    "DGS30": (SeriesKind.RATES, "30-Year Treasury constant maturity rate"),
    "T10Y2Y": (SeriesKind.RATES, "10-Year minus 2-Year Treasury spread"),
    "T10Y3M": (SeriesKind.RATES, "10-Year minus 3-Month Treasury spread"),
    "FEDFUNDS": (SeriesKind.RATES, "Federal Funds Effective Rate"),
    "SOFR": (SeriesKind.RATES, "Secured Overnight Financing Rate"),
    "DPRIME": (SeriesKind.RATES, "Bank Prime Loan Rate"),
    # Money supply (monetary)
    "M1SL": (SeriesKind.MONETARY, "M1 Money Stock"),
    "M2SL": (SeriesKind.MONETARY, "M2 Money Stock"),
    "M2V": (SeriesKind.MONETARY, "Velocity of M2 Money Stock"),
    "BOGMBASE": (SeriesKind.MONETARY, "Monetary Base"),
    "WALCL": (SeriesKind.MONETARY, "Fed total assets (balance sheet)"),
    # Inflation
    "CPIAUCSL": (SeriesKind.INFLATION, "Consumer Price Index All Urban"),
    "PCEPI": (SeriesKind.INFLATION, "Personal Consumption Expenditures Price Index"),
    "PCEPILFE": (SeriesKind.INFLATION, "Core PCE Price Index"),
    "PPIFIS": (SeriesKind.INFLATION, "Producer Price Index Final Demand"),
    # Employment
    "UNRATE": (SeriesKind.EMPLOYMENT, "Civilian Unemployment Rate"),
    "PAYEMS": (SeriesKind.EMPLOYMENT, "All Employees Total Nonfarm"),
    "JTSJOL": (SeriesKind.EMPLOYMENT, "Job Openings Total Nonfarm"),
    # Financial stress
    "STLFSI4": (SeriesKind.FINANCIAL_STRESS, "St. Louis Fed Financial Stress Index"),
    "NFCI": (
        SeriesKind.FINANCIAL_STRESS,
        "Chicago Fed National Financial Conditions Index",
    ),
    "VIXCLS": (SeriesKind.FINANCIAL_STRESS, "CBOE Volatility Index (VIX)"),
    "BAMLH0A0HYM2": (
        SeriesKind.FINANCIAL_STRESS,
        "ICE BofA US High Yield Option-Adjusted Spread",
    ),
    # Confidence
    "UMCSENT": (SeriesKind.CONFIDENCE, "University of Michigan Consumer Sentiment"),
    "NFLI": (SeriesKind.CONFIDENCE, "NFLI"),
    # FX
    "DTWEXBGS": (SeriesKind.FX, "Trade Weighted Dollar Index Broad"),
}


class FredProvider:
    """Async FRED macro data provider.

    Implements the MacroDataSource Protocol. Uses httpx.AsyncClient with
    connection pooling, retry, and stale-cache fallback.
    """

    def __init__(self, cfg: MacroDataSourceConfig) -> None:
        self._cfg = cfg
        api_key = cfg.api_key or os.environ.get("FRED_API_KEY")
        if not api_key:
            logger.warning(
                "FRED_API_KEY missing — FRED provider will healthcheck=False"
            )
        self._api_key = api_key
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
                base_url=self._cfg.base_url or FRED_BASE_URL,
                timeout=httpx.Timeout(
                    connect=5.0, read=self._cfg.timeout_seconds, write=10.0, pool=10.0
                ),
                proxy=proxy,
            )

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def healthcheck(self) -> bool:
        if not self._api_key or self._client is None:
            return False
        try:
            r = await self._client.get(
                "/series",
                params={
                    "series_id": "GDP",
                    "api_key": self._api_key,
                    "file_type": "json",
                },
            )
            return r.status_code == 200
        except Exception as exc:
            logger.warning("FRED healthcheck failed: %s", exc)
            return False

    async def fetch_series(
        self,
        series_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> MacroSeries:
        if self._client is None:
            await self.start()
        assert self._client is not None

        async def _do_fetch() -> MacroSeries:
            assert self._client is not None  # for mypy: started above
            params: dict[str, Any] = {
                "series_id": series_id,
                "api_key": self._api_key,
                "file_type": "json",
            }
            if start:
                params["observation_start"] = start.strftime("%Y-%m-%d")
            if end:
                params["observation_end"] = end.strftime("%Y-%m-%d")
            if limit:
                params["limit"] = limit

            r = await self._client.get("/series/observations", params=params)
            r.raise_for_status()
            payload = r.json()
            obs = payload.get("observations", [])
            df = pd.DataFrame(
                [
                    {
                        "date": pd.to_datetime(o["date"]),
                        "value": _parse_value(o["value"]),
                    }
                    for o in obs
                ]
            )
            kind, desc = FRED_CATALOG.get(series_id, (SeriesKind.OTHER, series_id))
            series = MacroSeries(
                provider="fred",
                series_id=series_id,
                kind=kind,
                description=desc,
                observations=df,
                fetched_at=datetime.now(UTC),
                source_url=f"{FRED_BASE_URL}/series/observations?series_id={series_id}",
            )
            self._cache[series_id] = series
            return series

        try:
            return await with_retry(_do_fetch)
        except Exception as exc:
            logger.error("FRED fetch %s failed: %s", series_id, exc)
            cached = self._cache.get(series_id)
            if cached is not None and is_cache_valid(
                cached, self._cfg.cache_ttl_seconds
            ):
                return cached
            return stale_fallback(cached, series_id)

    def get_cached(self, series_id: str) -> MacroSeries | None:
        cached = self._cache.get(series_id)
        if cached is None:
            return None
        if is_cache_valid(cached, self._cfg.cache_ttl_seconds):
            return cached
        return None  # expired — caller can still use stale_fallback if needed

    def list_series(self) -> list[str]:
        return list(FRED_CATALOG.keys())


def _parse_value(raw: Any) -> float | None:
    """FRED returns '.' for missing values."""
    if raw in (".", "", None):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def register() -> None:
    """Register the FRED provider under 'fred' id."""
    from selene_space_macrodata.registry import register_provider

    register_provider("fred", lambda cfg: FredProvider(cfg))


# Auto-register on import
register()

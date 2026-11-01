"""BIS provider — Bank for International Settlements SDMX API.

Translated from <local-path-redacted> + bis_data.py + bis_data_extended.py.
P0 stub: implements Protocol, healthcheck + fetch_series with retry/fallback.
Catalog limited to crypto-relevant series (effective exchange rates, policy rates, credit gap).

No API key required (BIS SDMX is public).
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

BIS_BASE_URL = "https://stats.bis.org/api/v1/data"

# Curated BIS series catalog (crypto-relevant).
# Format: series_id -> (kind, description, flow_ref, key)
# flow_ref + key verified against BIS SDMX REST API (2026-07-25).
# Key format uses '.' separator (SDMX REST convention), e.g. "M.N.B.US" not "M:US".
BIS_CATALOG: dict[str, tuple[SeriesKind, str, str, str]] = {
    # Effective exchange rates (WS_EER)
    "NEER_US": (
        SeriesKind.FX,
        "BIS Nominal Effective Exchange Rate — US",
        "WS_EER",
        "M.N.B.US",
    ),
    "REER_US": (
        SeriesKind.FX,
        "BIS Real Effective Exchange Rate — US",
        "WS_EER",
        "M.N.R.US",
    ),
    "NEER_CN": (
        SeriesKind.FX,
        "BIS Nominal Effective Exchange Rate — China",
        "WS_EER",
        "M.N.B.CN",
    ),
    # Central bank policy rates (WS_CBPOL)
    "POLICY_RATE_US": (
        SeriesKind.RATES,
        "BIS Policy Rate — US (Fed Funds)",
        "WS_CBPOL",
        "M.US",
    ),
    "POLICY_RATE_EU": (
        SeriesKind.RATES,
        "BIS Policy Rate — Euro Area (ECB)",
        "WS_CBPOL",
        "M.XU",
    ),
    "POLICY_RATE_CN": (
        SeriesKind.RATES,
        "BIS Policy Rate — China (PBoC)",
        "WS_CBPOL",
        "M.CN",
    ),
    # Credit-to-GDP gap (WS_CREDIT_GAP)
    "CREDIT_GAP_US": (
        SeriesKind.FINANCIAL_STRESS,
        "BIS Credit-to-GDP Gap — US",
        "WS_CREDIT_GAP",
        "Q.US",
    ),
}


class BisProvider:
    """Async BIS macro data provider.

    Implements MacroDataSource Protocol. No API key required.
    SDMX REST API: {flow}/{key}/all?format=sdmx-json
    """

    def __init__(self, cfg: MacroDataSourceConfig) -> None:
        self._cfg = cfg
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
                base_url=self._cfg.base_url or BIS_BASE_URL,
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
            r = await self._client.get(
                "/WS_CBPOL/M.US/all",
                params={"format": "sdmx-json", "startPeriod": "2024-01", "endPeriod": "2024-03"},
            )
            return r.status_code == 200
        except Exception as exc:
            logger.warning("BIS healthcheck failed: %s", exc)
            return False

    async def fetch_series(
        self,
        series_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> MacroSeries:
        if series_id not in BIS_CATALOG:
            raise KeyError(f"Unknown BIS series: {series_id}")
        if self._client is None:
            await self.start()
        assert self._client is not None

        kind, desc, flow_ref, key = BIS_CATALOG[series_id]

        async def _do_fetch() -> MacroSeries:
            assert self._client is not None  # for mypy: started above
            params: dict[str, Any] = {"format": "sdmx-json"}
            if start:
                params["startPeriod"] = start.strftime("%Y-%m")
            if end:
                params["endPeriod"] = end.strftime("%Y-%m")
            r = await self._client.get(f"/{flow_ref}/{key}/all", params=params)
            r.raise_for_status()
            payload = r.json()
            obs = _extract_observations(payload)
            df = pd.DataFrame(obs)
            series = MacroSeries(
                provider="bis",
                series_id=series_id,
                kind=kind,
                description=desc,
                observations=df,
                fetched_at=datetime.now(UTC),
                source_url=f"{BIS_BASE_URL}/{flow_ref}/{key}/all",
            )
            self._cache[series_id] = series
            return series

        try:
            return await with_retry(_do_fetch)
        except Exception as exc:
            logger.error("BIS fetch %s failed: %s", series_id, exc)
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
        return list(BIS_CATALOG.keys())


def _extract_observations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract observations from SDMX-JSON payload.

    BIS SDMX-JSON structure:
    data.dataSets[0].series["0:0"].observations = {"0": [value, ...], "1": [value, ...], ...}
    data.structure.dimensions.observation[0].values = [{id: "2024-01", name:...}, ...]
    The observation keys (0,1,2,...) index into the TIME_PERIOD dimension values.
    """
    data = payload.get("data", {})
    data_sets = data.get("dataSets", [])
    if not data_sets:
        return []

    # Build TIME_PERIOD index → period id mapping from structure.
    obs_dim_values: list[dict[str, Any]] = []
    dims = data.get("structure", {}).get("dimensions", {})
    for dim in dims.get("observation", []):
        if dim.get("id") == "TIME_PERIOD":
            obs_dim_values = dim.get("values", [])
            break
    if not obs_dim_values:
        return []

    # Series observations live under dataSets[0].series[key].observations
    series_dict = data_sets[0].get("series", {})
    rows: list[dict[str, Any]] = []
    if not series_dict:
        # Flat observations fallback (no series wrapper).
        flat_obs = data_sets[0].get("observations", {})
        for idx_str, values in flat_obs.items():
            idx = int(idx_str)
            if idx >= len(obs_dim_values) or not values:
                continue
            value = values[0]
            if value is None:
                continue
            period = obs_dim_values[idx].get("id")
            if not period:
                continue
            try:
                rows.append({"date": pd.to_datetime(period), "value": float(value)})
            except (TypeError, ValueError):
                continue
        rows.sort(key=lambda r: r["date"])
        return rows

    for _series_key, series_obj in series_dict.items():
        observations = series_obj.get("observations", {})
        for idx_str, values in observations.items():
            idx = int(idx_str)
            if idx >= len(obs_dim_values) or not values:
                continue
            value = values[0]
            if value is None:
                continue
            period = obs_dim_values[idx].get("id")
            if not period:
                continue
            try:
                rows.append({"date": pd.to_datetime(period), "value": float(value)})
            except (TypeError, ValueError):
                continue
    rows.sort(key=lambda r: r["date"])
    return rows


def register() -> None:
    from selene_space_macrodata.registry import register_provider

    register_provider("bis", lambda cfg: BisProvider(cfg))


register()

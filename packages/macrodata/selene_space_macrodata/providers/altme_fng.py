"""alternative.me Fear & Greed Index provider (crypto market sentiment).

GET https://api.alternative.me/fng/?limit=24 — free, no API key.
Returns the current value plus up to 24h of daily history (API returns
newest-first; observations are re-sorted ascending so the last row is current).
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

ALTME_BASE_URL = "https://api.alternative.me"
FNG_SERIES_ID = "FNG"
FNG_HISTORY_LIMIT = 24

SERIES_CATALOG: dict[str, tuple[SeriesKind, str]] = {
    FNG_SERIES_ID: (SeriesKind.CONFIDENCE, "alternative.me Crypto Fear & Greed Index"),
}


class AltmeFngProvider:
    """Async alternative.me F&G provider implementing MacroDataSource."""

    def __init__(
        self,
        cfg: MacroDataSourceConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self._cfg = cfg
        self._transport = transport
        self._retry_backoff_seconds = retry_backoff_seconds
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
            # httpx silently drops `transport` when `proxy` is also set —
            # an injected transport (tests) must take precedence over env proxy.
            if self._transport is not None:
                self._client = httpx.AsyncClient(
                    base_url=self._cfg.base_url or ALTME_BASE_URL,
                    timeout=httpx.Timeout(
                        connect=5.0,
                        read=self._cfg.timeout_seconds,
                        write=10.0,
                        pool=10.0,
                    ),
                    transport=self._transport,
                )
            else:
                proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
                self._client = httpx.AsyncClient(
                    base_url=self._cfg.base_url or ALTME_BASE_URL,
                    timeout=httpx.Timeout(
                        connect=5.0,
                        read=self._cfg.timeout_seconds,
                        write=10.0,
                        pool=10.0,
                    ),
                    proxy=proxy,
                )

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def healthcheck(self) -> bool:
        if self._client is None:
            await self.start()
        assert self._client is not None
        try:
            r = await self._client.get("/fng/", params={"limit": 1})
            return r.status_code == 200
        except Exception as exc:
            logger.warning("alt.me F&G healthcheck failed: %s", exc)
            return False

    async def fetch_series(
        self,
        series_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> MacroSeries:
        if series_id not in SERIES_CATALOG:
            raise KeyError(f"unknown series {series_id!r} for altme_fng")
        if self._client is None:
            await self.start()
        assert self._client is not None

        async def _do_fetch() -> MacroSeries:
            r = await self._client.get("/fng/", params={"limit": FNG_HISTORY_LIMIT})  # type: ignore[union-attr]
            r.raise_for_status()
            entries: list[dict[str, Any]] = r.json().get("data", [])
            rows: list[dict[str, Any]] = [
                {
                    "date": datetime.fromtimestamp(int(e["timestamp"]), tz=UTC),
                    "value": int(float(e["value"])),
                    "classification": str(e.get("value_classification", "")),
                }
                for e in entries
                if e.get("timestamp") is not None and e.get("value") is not None
            ]
            rows.sort(key=lambda row: row["date"])
            if not rows:
                raise ValueError("alternative.me F&G payload has no usable entries")
            kind, desc = SERIES_CATALOG[series_id]
            series = MacroSeries(
                provider="altme_fng",
                series_id=series_id,
                kind=kind,
                description=desc,
                observations=pd.DataFrame(rows),
                fetched_at=datetime.now(UTC),
                source_url=f"{ALTME_BASE_URL}/fng/?limit={FNG_HISTORY_LIMIT}",
            )
            self._cache[series_id] = series
            return series

        try:
            return await with_retry(
                _do_fetch, backoff_seconds=self._retry_backoff_seconds
            )
        except Exception as exc:
            logger.error("alt.me F&G fetch %s failed: %s", series_id, exc)
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
        return cached if is_cache_valid(cached, self._cfg.cache_ttl_seconds) else None

    def list_series(self) -> list[str]:
        return list(SERIES_CATALOG)


def register() -> None:
    """Register the alternative.me F&G provider under 'altme_fng' id."""
    from selene_space_macrodata.registry import register_provider

    register_provider("altme_fng", lambda cfg: AltmeFngProvider(cfg))


register()

# Absorbed from US_Stock_Crash_Monitor (MIT License (c) 2025 竹林寺/middletoo)
# Source: data_fetcher.py get_fear_greed(), upstream commit a7e5003cbd6521c2d5e4b5f9d972aab404d45e57
"""CNN Fear & Greed Index provider (US equity market sentiment).

Backup source in the F&G degradation chain (alt.me is primary; the two score
the same 0-100 scale with different anchors — consumers must keep the
`provider` field distinct and never blend the two series).

The CNN dataviz endpoint is unofficial and rejects requests without a full
browser User-Agent and Referer with HTTP 418 (verified upstream). Endpoint
constants are converged here so an endpoint rework is a single-file fix.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any, ClassVar

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

CNN_BASE_URL = "https://production.dataviz.cnn.io"
CNN_FNG_PATH = "/index/fearandgreed/graphdata"
CNN_FNG_URL = f"{CNN_BASE_URL}{CNN_FNG_PATH}"
# Full browser UA + Referer are required — a bare client UA gets HTTP 418.
CNN_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
CNN_REFERER = "https://www.cnn.com/"

FNG_SERIES_ID = "FNG_US"

SERIES_CATALOG: dict[str, tuple[SeriesKind, str]] = {
    FNG_SERIES_ID: (SeriesKind.CONFIDENCE, "CNN Fear & Greed Index (US equities)"),
}


class CnnFngEndpointError(RuntimeError):
    """CNN endpoint rejected the request (e.g. HTTP 418) — source unavailable, not a network failure."""


class CnnFngProvider:
    """Async CNN F&G provider implementing MacroDataSource."""

    DEFAULT_HEADERS: ClassVar[dict[str, str]] = {
        "User-Agent": CNN_USER_AGENT,
        "Referer": CNN_REFERER,
        "Accept": "application/json,text/html,application/xhtml+xml,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

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
                    base_url=self._cfg.base_url or CNN_BASE_URL,
                    timeout=httpx.Timeout(
                        connect=5.0,
                        read=self._cfg.timeout_seconds,
                        write=10.0,
                        pool=10.0,
                    ),
                    headers=self.DEFAULT_HEADERS,
                    transport=self._transport,
                )
            else:
                proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
                self._client = httpx.AsyncClient(
                    base_url=self._cfg.base_url or CNN_BASE_URL,
                    timeout=httpx.Timeout(
                        connect=5.0,
                        read=self._cfg.timeout_seconds,
                        write=10.0,
                        pool=10.0,
                    ),
                    headers=self.DEFAULT_HEADERS,
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
            r = await self._client.get(CNN_FNG_PATH)
            return r.status_code == 200
        except Exception as exc:
            logger.warning("CNN F&G healthcheck failed: %s", exc)
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
            raise KeyError(f"unknown series {series_id!r} for cnn_fng")
        if self._client is None:
            await self.start()
        assert self._client is not None

        async def _do_fetch() -> MacroSeries:
            r = await self._client.get(CNN_FNG_PATH)  # type: ignore[union-attr]
            if r.status_code == 418:
                raise CnnFngEndpointError(
                    "CNN F&G endpoint returned 418 (UA/Referer rejected) — endpoint contract changed"
                )
            r.raise_for_status()
            fg: dict[str, Any] = r.json().get("fear_and_greed", {})
            score = fg.get("score")
            if score is None:
                raise ValueError("CNN F&G payload missing 'fear_and_greed.score'")
            row = {
                "date": datetime.now(UTC),
                "value": int(round(float(score))),
                "rating": str(fg.get("rating", "")),
                "timestamp": str(fg.get("timestamp", "")),
            }
            kind, desc = SERIES_CATALOG[series_id]
            series = MacroSeries(
                provider="cnn_fng",
                series_id=series_id,
                kind=kind,
                description=desc,
                observations=pd.DataFrame([row]),
                fetched_at=datetime.now(UTC),
                source_url=CNN_FNG_URL,
            )
            self._cache[series_id] = series
            return series

        try:
            return await with_retry(
                _do_fetch, backoff_seconds=self._retry_backoff_seconds
            )
        except CnnFngEndpointError:
            logger.error(
                "CNN F&G endpoint rejected request for %s — endpoint contract changed",
                series_id,
            )
            cached = self._cache.get(series_id)
            if cached is not None and is_cache_valid(
                cached, self._cfg.cache_ttl_seconds
            ):
                return cached
            raise
        except Exception as exc:
            logger.error("CNN F&G fetch %s failed: %s", series_id, exc)
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
    """Register the CNN F&G provider under 'cnn_fng' id."""
    from selene_space_macrodata.registry import register_provider

    register_provider("cnn_fng", lambda cfg: CnnFngProvider(cfg))


register()

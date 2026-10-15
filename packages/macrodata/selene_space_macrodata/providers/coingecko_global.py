"""CoinGecko global crypto market provider (btc dominance + total market cap).

GET https://api.coingecko.com/api/v3/global — free, no API key.
Rate limited to ~5-15 req/min on the free tier; callers should rely on the
cache TTL rather than polling.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

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

COINGECKO_BASE_URL = "https://api.coingecko.com"
GLOBAL_PATH = "/api/v3/global"

BTC_DOMINANCE = "BTC_DOMINANCE"
TOTAL_MARKET_CAP = "TOTAL_MARKET_CAP"

SERIES_CATALOG: dict[str, tuple[SeriesKind, str]] = {
    BTC_DOMINANCE: (SeriesKind.OTHER, "Bitcoin share of total crypto market cap (%)"),
    TOTAL_MARKET_CAP: (SeriesKind.OTHER, "Total crypto market cap (USD)"),
}


class CoingeckoGlobalProvider:
    """Async CoinGecko /global provider implementing MacroDataSource."""

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
                    base_url=self._cfg.base_url or COINGECKO_BASE_URL,
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
                    base_url=self._cfg.base_url or COINGECKO_BASE_URL,
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
            r = await self._client.get(GLOBAL_PATH)
            return r.status_code == 200
        except Exception as exc:
            logger.warning("CoinGecko global healthcheck failed: %s", exc)
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
            raise KeyError(f"unknown series {series_id!r} for coingecko_global")
        if self._client is None:
            await self.start()
        assert self._client is not None

        async def _do_fetch() -> MacroSeries:
            r = await self._client.get(GLOBAL_PATH)  # type: ignore[union-attr]
            r.raise_for_status()
            data: dict = r.json().get("data", {})
            mcap_pct = data.get("market_cap_percentage", {}).get("btc")
            total_cap = data.get("total_market_cap", {}).get("usd")
            if series_id == BTC_DOMINANCE:
                if mcap_pct is None:
                    raise ValueError("CoinGecko /global payload missing btc_dominance")
                value: float = float(mcap_pct)
            else:
                if total_cap is None:
                    raise ValueError(
                        "CoinGecko /global payload missing total_market_cap.usd"
                    )
                value = float(total_cap)
            series = MacroSeries(
                provider="coingecko_global",
                series_id=series_id,
                kind=SERIES_CATALOG[series_id][0],
                description=SERIES_CATALOG[series_id][1],
                observations=pd.DataFrame(
                    [{"date": datetime.now(UTC), "value": value}]
                ),
                fetched_at=datetime.now(UTC),
                source_url=f"{COINGECKO_BASE_URL}{GLOBAL_PATH}",
            )
            self._cache[series_id] = series
            return series

        try:
            return await with_retry(
                _do_fetch, backoff_seconds=self._retry_backoff_seconds
            )
        except Exception as exc:
            logger.error("CoinGecko global fetch %s failed: %s", series_id, exc)
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
    """Register the CoinGecko global provider under 'coingecko_global' id."""
    from selene_space_macrodata.registry import register_provider

    register_provider("coingecko_global", lambda cfg: CoingeckoGlobalProvider(cfg))


register()

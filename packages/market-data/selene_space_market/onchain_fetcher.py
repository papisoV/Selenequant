"""Glassnode on-chain metrics: active addresses, NVT, SOPR.

``OnchainFetcher`` wraps three read-only Glassnode endpoints behind one
cached interface. Each metric is cached independently under
``"SYMBOL:metric"``; a missing ``GLASSNODE_API_KEY`` degrades to serving the
last cached value (or None on first call) instead of erroring.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass

import httpx

from selene_space_core.resilience import OutboundGateway

logger = logging.getLogger(__name__)

GLASSNODE_BASE_URL = "https://api.glassnode.com/v1/metrics"
CACHE_TTL_MS = 300_000


@dataclass(frozen=True)
class OnchainMetrics:
    symbol: str
    metric: str
    value: float
    timestamp: int


# public name -> glassnode endpoint path
_METRIC_ENDPOINTS: dict[str, str] = {
    "active_addresses": "addresses/active_count",
    "nvt_ratio": "indicators/nvt",
    "sopr": "indicators/sopr",
}


class OnchainFetcher:
    def __init__(self, *, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._gateway: OutboundGateway | None = None
        self._by_metric: dict[str, OnchainMetrics] = {}
        self._refresh_lock = asyncio.Lock()

    async def _get_client(self) -> OutboundGateway:
        if self._gateway is None or self._gateway.is_closed:
            self._gateway = OutboundGateway(
                proxy=self._proxy_url,
                default_timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                default_circuit=True,
            )
        return self._gateway

    async def get_active_addresses(self, symbol: str = "BTC") -> OnchainMetrics | None:
        return await self.get_metric(symbol, "active_addresses")

    async def get_nvt_ratio(self, symbol: str = "BTC") -> OnchainMetrics | None:
        return await self.get_metric(symbol, "nvt_ratio")

    async def get_sopr(self, symbol: str = "BTC") -> OnchainMetrics | None:
        return await self.get_metric(symbol, "sopr")

    async def get_metric(
        self,
        symbol: str,
        metric: str,
        *,
        interval: str = "24h",
        force: bool = False,
    ) -> OnchainMetrics | None:
        """Fetch one metric by its cache name (see ``_METRIC_ENDPOINTS``)."""
        endpoint = _METRIC_ENDPOINTS.get(metric)
        if endpoint is None:
            raise ValueError(f"Unknown on-chain metric: {metric!r}")

        if not force:
            hit = self._fresh(symbol, metric)
            if hit is not None:
                return hit

        async with self._refresh_lock:
            if not force:
                hit = self._fresh(symbol, metric)
                if hit is not None:
                    return hit
            return await self._download(symbol, metric, endpoint, interval)

    def _fresh(self, symbol: str, metric: str) -> OnchainMetrics | None:
        entry = self._by_metric.get(f"{symbol}:{metric}")
        if entry and (int(time.time() * 1000) - entry.timestamp * 1000) < CACHE_TTL_MS:
            return entry
        return None

    async def _download(
        self, symbol: str, metric: str, endpoint: str, interval: str
    ) -> OnchainMetrics | None:
        api_key = os.environ.get("GLASSNODE_API_KEY", "")
        if not api_key:
            logger.warning("GLASSNODE_API_KEY unset — serving stale %s/%s", symbol, metric)
            return self._by_metric.get(f"{symbol}:{metric}")

        client = await self._get_client()
        try:
            resp = await client.get(
                f"{GLASSNODE_BASE_URL}/{endpoint}",
                params={"a": symbol.upper(), "i": interval, "api_key": api_key},
            )
            resp.raise_for_status()
            series = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("glassnode HTTP %s for %s/%s", exc.response.status_code, symbol, metric)
            return self._by_metric.get(f"{symbol}:{metric}")
        except Exception as exc:
            logger.error("glassnode request failed for %s/%s: %s", symbol, metric, exc)
            return self._by_metric.get(f"{symbol}:{metric}")

        if not isinstance(series, list) or not series:
            logger.warning("glassnode returned no points for %s/%s", symbol, metric)
            return self._by_metric.get(f"{symbol}:{metric}")

        latest = series[-1]
        try:
            result = OnchainMetrics(
                symbol=symbol,
                metric=metric,
                value=float(latest.get("v", 0)),
                timestamp=int(latest.get("t", 0)),
            )
        except (TypeError, ValueError):
            logger.warning("glassnode malformed point for %s/%s: %r", symbol, metric, latest)
            return self._by_metric.get(f"{symbol}:{metric}")

        self._by_metric[f"{symbol}:{metric}"] = result
        logger.info("glassnode %s/%s = %.4f @%d", symbol, metric, result.value, result.timestamp)
        return result

    async def close(self) -> None:
        if self._gateway and not self._gateway.is_closed:
            await self._gateway.aclose()
            self._gateway = None

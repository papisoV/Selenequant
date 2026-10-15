"""Birdeye market data provider for Solana DEX tokens.

Fetches trending tokens, OHLCV data, and metadata from Birdeey API.
Requires BIRDEYE_API_KEY in .env.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://public-api.birdeye.so"


class BirdeyeProvider:
    """Async Birdeye API client for Solana DEX market data."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.getenv("BIRDEYE_API_KEY", "")
        if not self._api_key:
            logger.warning("BIRDEYE_API_KEY not set — Birdeye calls will fail")
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"X-API-KEY": self._api_key} if self._api_key else {}
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(connect=10, total=30),
            )
        return self._session

    async def get_trending_tokens(
        self, chain: str = "solana", limit: int = 50, sort_by: str = "rank"
    ) -> list[dict[str, Any]]:
        """Fetch trending/new tokens from Birdeye."""
        session = await self._get_session()
        url = f"{BASE_URL}/defi/token_trending"
        params = {"chain": chain, "limit": str(limit), "sort_by": sort_by}
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error("Birdeye trending error: %s", text)
                return []
            data = await resp.json()
            return data.get("data", {}).get("tokens", [])

    async def get_ohlcv(
        self,
        address: str,
        time_frame: str = "1H",
        limit: int = 100,
    ) -> list[tuple[Any, ...]]:
        """Fetch OHLCV data for a token.

        Returns list of (time, address, open, high, low, close, volume, liquidity, fdv, source) tuples.
        """
        session = await self._get_session()
        url = f"{BASE_URL}/defi/ohlcv"
        params = {
            "address": address,
            "type": time_frame,
            "time_from": "0",
            "count": str(limit),
        }
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error("Birdeye OHLCV error: %s", text)
                return []
            data = await resp.json()
            items = data.get("data", {}).get("items", [])
            results = []
            for item in items:
                unix_time = item.get("unixTime", 0)
                o = item.get("o", 0.0)
                h = item.get("h", 0.0)
                l_ = item.get("l", 0.0)
                c = item.get("c", 0.0)
                v = item.get("v", 0.0)
                liq = item.get("liquidity", 0.0)
                fdv = item.get("mcap", 0.0)
                results.append(
                    (unix_time, address, o, h, l_, c, v, liq, fdv, "birdeye")
                )
            return results

    async def get_token_metadata(self, address: str) -> dict[str, Any]:
        """Fetch token metadata (symbol, decimals, etc.)."""
        session = await self._get_session()
        url = f"{BASE_URL}/defi/token_overview/{address}"
        async with session.get(url) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()
            return data.get("data", {})

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

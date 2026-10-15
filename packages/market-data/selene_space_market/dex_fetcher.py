"""DexScreener DEX pair data: per-chain tops, token lookup, search.

``DexFetcher`` talks to the keyless ``api.dexscreener.com/latest/dex``
endpoints and normalizes raw pair payloads into ``DexPair`` records. Chain
listings and searches are cached for a short TTL because DEX state moves
fast; single-pair lookups go straight through.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from selene_space_core.resilience import OutboundGateway

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dexscreener.com"
CACHE_TTL_MS = 15_000
_SEARCH_CAP = 50


@dataclass(frozen=True)
class DexPair:
    chain_id: str
    dex_id: str
    pair_address: str
    base_token_symbol: str
    base_token_address: str
    quote_token_symbol: str
    quote_token_address: str
    price_usd: float | None
    price_native: float
    volume_24h: float
    liquidity_usd: float | None
    tx_count_24h: int
    price_change_24h: float | None
    url: str | None
    timestamp: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "dex_id": self.dex_id,
            "pair_address": self.pair_address,
            "base_token_symbol": self.base_token_symbol,
            "base_token_address": self.base_token_address,
            "quote_token_symbol": self.quote_token_symbol,
            "quote_token_address": self.quote_token_address,
            "price_usd": self.price_usd,
            "price_native": self.price_native,
            "volume_24h": self.volume_24h,
            "liquidity_usd": self.liquidity_usd,
            "tx_count_24h": self.tx_count_24h,
            "price_change_24h": self.price_change_24h,
            "url": self.url,
            "timestamp": self.timestamp,
        }


def _opt_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float:
    return _opt_float(value) or 0.0


def _int(value: Any) -> int:
    try:
        return int(float(value)) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _parse_pair(raw: dict[str, Any], fallback_chain: str, stamp: int) -> DexPair | None:
    """One raw DexScreener pair object -> DexPair (None if unusable)."""
    dex_id = raw.get("dexId", "")
    address = raw.get("pairAddress", "")
    if not dex_id or not address:
        return None

    base = raw.get("baseToken") or {}
    quote = raw.get("quoteToken") or {}

    h24_txns = (raw.get("txns") or {}).get("h24") or {}
    h24_change = (raw.get("priceChange") or {}).get("h24")

    return DexPair(
        chain_id=fallback_chain or raw.get("chainId", ""),
        dex_id=dex_id,
        pair_address=address,
        base_token_symbol=base.get("symbol", ""),
        base_token_address=base.get("address", ""),
        quote_token_symbol=quote.get("symbol", ""),
        quote_token_address=quote.get("address", ""),
        price_usd=_opt_float(raw.get("priceUsd")),
        price_native=_float(raw.get("priceNative")),
        volume_24h=_float((raw.get("volume") or {}).get("h24")),
        liquidity_usd=_opt_float((raw.get("liquidity") or {}).get("usd")),
        tx_count_24h=_int(h24_txns.get("buys")) + _int(h24_txns.get("sells")),
        price_change_24h=_opt_float(h24_change),
        url=raw.get("url"),
        timestamp=stamp,
    )


class DexFetcher:
    def __init__(self, *, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._gateway: OutboundGateway | None = None
        # cache slot: chain id, or "search:<query>" — value is (at_ms, pairs)
        self._slots: dict[str, tuple[int, list[DexPair]]] = {}
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

    def _cached(self, slot: str) -> list[DexPair] | None:
        entry = self._slots.get(slot)
        if entry and entry[0] and len(entry[1]) > 0 and (int(time.time() * 1000) - entry[0]) < CACHE_TTL_MS:
            return entry[1]
        return None

    async def get_pairs_by_chain(self, chain: str) -> list[DexPair]:
        """Top pairs on one chain, liquidity descending."""
        slot = chain.lower()
        hit = self._cached(slot)
        if hit is not None:
            return hit

        async with self._refresh_lock:
            hit = self._cached(slot)
            if hit is not None:
                return hit

            body = await self._request(f"/latest/dex/pairs/{slot}")
            stamp = int(time.time() * 1000)
            pairs = self._parse_pairs(body, slot, stamp)
            pairs.sort(key=lambda p: p.liquidity_usd or 0.0, reverse=True)

            if pairs:
                self._slots[slot] = (stamp, pairs)
            logger.info("dexscreener chain %s: %d pairs", slot, len(pairs))
            return pairs

    async def search_pairs(self, query: str) -> list[DexPair]:
        """Cross-chain token-name search, volume descending, capped at 50."""
        slot = f"search:{query.lower()}"
        hit = self._cached(slot)
        if hit is not None:
            return hit

        async with self._refresh_lock:
            hit = self._cached(slot)
            if hit is not None:
                return hit

            body = await self._request("/latest/dex/search", params={"q": query})
            stamp = int(time.time() * 1000)
            pairs = self._parse_pairs(body, "", stamp)
            pairs.sort(key=lambda p: p.volume_24h, reverse=True)
            pairs = pairs[:_SEARCH_CAP]

            if pairs:
                self._slots[slot] = (stamp, pairs)
            logger.info("dexscreener search %r: %d pairs", query, len(pairs))
            return pairs

    async def get_pair(self, chain: str, pair_address: str) -> DexPair | None:
        body = await self._request(f"/latest/dex/pairs/{chain.lower()}/{pair_address}")
        stamp = int(time.time() * 1000)
        pairs = self._parse_pairs(body, chain.lower(), stamp)
        return pairs[0] if pairs else None

    async def get_token_pairs(self, chain: str, token_address: str) -> list[DexPair]:
        """Every pool listing one token on a chain, liquidity descending."""
        body = await self._request(f"/latest/dex/tokens/{token_address}")
        stamp = int(time.time() * 1000)
        pairs = self._parse_pairs(body, chain.lower(), stamp)
        pairs.sort(key=lambda p: p.liquidity_usd or 0.0, reverse=True)
        logger.info("dexscreener token %s on %s: %d pairs", token_address, chain, len(pairs))
        return pairs[:_SEARCH_CAP]

    @staticmethod
    def _parse_pairs(body: dict[str, Any], chain_hint: str, stamp: int) -> list[DexPair]:
        rows = body.get("pairs")
        if not isinstance(rows, list):
            return []
        parsed: list[DexPair] = []
        for row in rows:
            if isinstance(row, dict):
                pair = _parse_pair(row, chain_hint, stamp)
                if pair is not None:
                    parsed.append(pair)
        return parsed

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = await self._get_client()
        try:
            resp = await client.get(f"{BASE_URL}{path}", params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("dexscreener HTTP %s for %s", exc.response.status_code, path)
        except Exception as exc:
            logger.error("dexscreener request failed for %s: %s", path, exc)
        return {}

    async def close(self) -> None:
        if self._gateway and not self._gateway.is_closed:
            await self._gateway.aclose()
            self._gateway = None

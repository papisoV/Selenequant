"""Polymarket CLOB read-only REST client.

Public endpoints only — markets, books, midpoint/price, server time. The
venue sits behind Cloudflare and local proxies often fail TLS, so the
client probes a candidate proxy once and falls back to a direct
connection; transient failures get a small bounded retry.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

import httpx

from selene_space_exchange.exchanges.polymarket.models import (
    PolymarketMarket,
    PolymarketOrderBook,
    PolymarketPrice,
)

_log = logging.getLogger(__name__)

DEFAULT_CLOB_URL = "https://clob.polymarket.com"
DEFAULT_GAMMA_URL = "https://gamma-api.polymarket.com"

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)
_MAX_RETRIES = 2
_RETRY_BASE_DELAY_S = 0.5

_PROXY_ENV_VARS = ("SELENE_PROXY_URL", "SELENE_POLYMARKET_PROXY")

#: Windows registry location of the system proxy settings.
_WINREG_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def _env_proxy() -> str | None:
    """SELENE_FORCE_PROXY (raw host:port allowed) then the standard vars."""
    forced = os.environ.get("SELENE_FORCE_PROXY", "").strip()
    if forced:
        scheme = "http://" if "://" not in forced else ""
        return f"{scheme}{forced}"
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", *_PROXY_ENV_VARS):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return None


def _registry_proxy() -> tuple[str, bool] | None:
    """(server, enabled) from the Windows registry, or None off-Windows."""
    if sys.platform != "win32":
        return None
    try:
        import winreg  # noqa: PLC0415

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WINREG_KEY_PATH) as handle:
            enable_value, _ = winreg.QueryValueEx(handle, "ProxyEnable")
            server_value, _ = winreg.QueryValueEx(handle, "ProxyServer")
        return str(server_value), bool(enable_value)
    except OSError:
        return None


def detect_local_proxy() -> str | None:
    """Resolve a usable local proxy: env first, then Windows settings.

    SELENE_FORCE_PROXY short-circuits everything. A registry proxy that is
    recorded but disabled is only used if a connectivity probe succeeds.
    """
    env_hit = _env_proxy()
    if env_hit and os.environ.get("SELENE_FORCE_PROXY"):
        return env_hit

    found = _registry_proxy()
    if found is not None:
        server, enabled = found
        candidate = f"http://{server}" if "://" not in server else server
        if enabled or _test_proxy_connectivity(candidate):
            if not env_hit:
                return candidate

    return env_hit


def _test_proxy_connectivity(proxy: str) -> bool:
    try:
        with httpx.Client(proxy=proxy, timeout=4.0) as probe:
            resp = probe.get("https://clob.polymarket.com/")
            return resp.status_code < 500
    except Exception:
        return False


class PolymarketClient:
    def __init__(
        self,
        *,
        clob_url: str = DEFAULT_CLOB_URL,
        gamma_url: str = DEFAULT_GAMMA_URL,
        timeout: httpx.Timeout | float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._clob = clob_url.rstrip("/")
        self._gamma = gamma_url.rstrip("/")
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None
        self._direct: httpx.AsyncClient | None = None

    @staticmethod
    def _detect_local_proxy() -> str | None:
        return detect_local_proxy()

    async def close(self) -> None:
        for client in (self._http, self._direct):
            if client is not None:
                await client.aclose()
        self._http = None
        self._direct = None

    def _get_direct_client(self) -> httpx.AsyncClient:
        if self._direct is None:
            self._direct = httpx.AsyncClient(timeout=self._timeout)
        return self._direct

    def _primary_client(self) -> httpx.AsyncClient:
        if self._http is None:
            proxy = detect_local_proxy()
            if proxy is not None:
                self._http = httpx.AsyncClient(proxy=proxy, timeout=self._timeout)
            else:
                self._http = self._get_direct_client()
        return self._http

    async def _try_get(
        self, client: httpx.AsyncClient, url: str, params: dict[str, Any] | None
    ) -> httpx.Response:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp

    async def _get_with_retry(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> httpx.Response:
        primary = self._primary_client()
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                if primary is self._http:
                    try:
                        return await self._try_get(primary, url, params)
                    except Exception as exc:
                        # Proxy path failed — remember and fall through to direct.
                        last_error = exc
                        primary = self._get_direct_client()
                        self._http = primary
                        continue
                return await self._try_get(primary, url, params)
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_error = exc
                if attempt < max_retries:
                    await asyncio.sleep(_RETRY_BASE_DELAY_S * (2**attempt))
        assert last_error is not None
        raise last_error

    # -- markets -----------------------------------------------------------------

    async def list_markets(
        self, *, limit: int = 20, next_cursor: str = ""
    ) -> list[PolymarketMarket]:
        params: dict[str, Any] = {"limit": limit}
        if next_cursor:
            params["next_cursor"] = next_cursor
        rows = (await self._get_with_retry(f"{self._gamma}/markets", params)).json()
        markets: list[PolymarketMarket] = []
        for row in rows or []:
            tokens = [
                {
                    "token_id": t.get("token_id", ""),
                    "outcome": t.get("outcome", ""),
                    "price": float(t.get("price") or 0.0),
                    "winner": bool(t.get("winner", False)),
                }
                for t in row.pop("tokens", None) or []
            ]
            # Venue spells the 24h figure differently from our model.
            row["volume_24h"] = float(row.pop("volume_num_24hr", 0.0) or 0.0)
            markets.append(PolymarketMarket(**row, tokens=tokens))
        return markets

    # -- book / prices --------------------------------------------------------------

    async def get_orderbook(self, token_id: str) -> PolymarketOrderBook:
        data = (
            await self._get_with_retry(f"{self._clob}/book", {"token_id": token_id})
        ).json()
        return PolymarketOrderBook(**data)

    async def get_midpoint(self, token_id: str) -> float:
        data = (
            await self._get_with_retry(f"{self._clob}/midpoint", {"token_id": token_id})
        ).json()
        return float(data["mid"])

    async def get_price(self, token_id: str, side: str = "BUY") -> float:
        data = (
            await self._get_with_retry(
                f"{self._clob}/price", {"token_id": token_id, "side": side}
            )
        ).json()
        return float(data["price"])

    async def get_last_trade_price(self, token_id: str) -> PolymarketPrice:
        data = (
            await self._get_with_retry(
                f"{self._clob}/last-trade-price", {"token_id": token_id}
            )
        ).json()
        return PolymarketPrice(token_id=token_id, price=float(data["price"]), side=data.get("side", ""))

    # -- events / search ---------------------------------------------------------------

    async def list_events(
        self,
        *,
        limit: int = 10,
        active: bool = True,
        closed: bool = False,
        order: str = "volume24hr",
        ascending: bool = False,
        end_date_min: str = "",
        end_date_max: str = "",
        start_date_min: str = "",
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "limit": limit,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": order,
            "ascending": str(ascending).lower(),
        }
        for key, value in (
            ("end_date_min", end_date_min),
            ("end_date_max", end_date_max),
            ("start_date_min", start_date_min),
        ):
            if value:
                params[key] = value
        result: list[dict[str, Any]] = (
            await self._get_with_retry(f"{self._gamma}/events", params)
        ).json()
        return result

    async def get_event(self, event_id: str) -> dict[str, Any]:
        result: dict[str, Any] = (
            await self._get_with_retry(f"{self._gamma}/events/{event_id}")
        ).json()
        return result

    async def get_event_by_slug(self, slug: str) -> dict[str, Any] | None:
        rows: list[dict[str, Any]] = (
            await self._get_with_retry(f"{self._gamma}/events", {"slug": slug})
        ).json()
        return rows[0] if rows else None

    async def search_markets_gamma(
        self,
        *,
        query: str = "",
        limit: int = 20,
        active: bool = True,
        closed: bool = False,
        order: str = "volume24hr",
        ascending: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "limit": limit,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": order,
            "ascending": str(ascending).lower(),
        }
        if query:
            params["q"] = query
        rows: list[dict[str, Any]] = (
            await self._get_with_retry(f"{self._gamma}/markets", params)
        ).json()
        return rows

    # -- misc -----------------------------------------------------------------------------

    async def get_server_time(self) -> int:
        resp = await self._get_with_retry(f"{self._clob}/time")
        return int(str(resp.text).strip())

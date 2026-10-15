"""Yahoo Finance async client — US stock research data (方向4).

Research-terminal quote/chart/news only. No trading, no custody, no client
funds movement (红线5 decision: 研究数据允许 / 交易代客禁止, 2026-08-15
user-confirmed).

Method signatures mirror the call sites in ``app/api/v1/us_radar.py``:
get_quote / get_news / get_chart(interval=, range_=) / get_market_summary /
get_movers / get_trending.
"""

from __future__ import annotations

from typing import Any

import httpx

from selene_space_core.resilience import OutboundGateway

YAHOO_BASE = "https://query1.finance.yahoo.com"
_YAHOO_COOKIE_URL = "https://fc.yahoo.com"  # sets the auth cookie (best-effort)
_CRUMB_PATH = "/v1/test/getcrumb"  # needs the cookie -> plain-text crumb
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SeleneQuant/1.0 (research)"
_INDEX_UNIVERSE = ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX"]
# 401/403 = "Invalid Crumb", 429 = rate-limited — both warrant a cookie+crumb refresh
_RETRYABLE_STATUS = {401, 403, 429}


class YahooFinanceError(Exception):
    """Raised when a Yahoo Finance call fails (network / HTTP / parse)."""


class YahooFinanceClient:
    """Minimal async client for Yahoo Finance public endpoints.

    ``trust_env=False`` so ambient OS proxies do not hijack requests (same
    lesson as the Eastmoney proxy block on this host).
    """

    def __init__(
        self,
        timeout: httpx.Timeout | None = None,
        *,
        client: OutboundGateway | None = None,
    ) -> None:
        # OutboundGateway: unified pool, per-host circuit breaker, thread
        # isolation — a stalled Yahoo endpoint cannot block the event loop.
        # NOTE: gateway has no base_url/per-client headers; call sites build
        # absolute URLs and pass the User-Agent per request below.
        self._client = client or OutboundGateway(
            default_timeout=timeout
            or httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
            default_circuit=True,
        )
        self._crumb: str | None = None
        self._auth_attempted = False

    async def close(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    # -- public methods (aligned to us_radar.py call sites) -----------------

    async def get_quote(self, symbols: list[str]) -> dict[str, Any]:
        """Quotes for one or more symbols -> {"quoteResponse": {"result": [...]}}."""
        if not symbols:
            return {"quoteResponse": {"result": []}}
        data = await self._get(
            "/v7/finance/quote", params={"symbols": ",".join(symbols)}
        )
        return data if isinstance(data, dict) else {}

    async def get_news(self, symbol: str) -> dict[str, Any]:
        """Recent news for a symbol -> {"news": [...]}."""
        data = await self._get(
            "/v1/finance/search", params={"q": symbol, "newsCount": 10}
        )
        return data if isinstance(data, dict) else {}

    async def get_chart(
        self, symbol: str, interval: str = "1d", range_: str = "3mo"
    ) -> dict[str, Any]:
        """Candles for a symbol -> {"chart": {"result": [...]}}."""
        data = await self._get(
            f"/v8/finance/chart/{symbol}",
            params={"interval": interval, "range": range_},
        )
        return data if isinstance(data, dict) else {}

    async def get_market_summary(self) -> dict[str, Any]:
        """Major US indices snapshot -> {"quoteResponse": {"result": [...]}}."""
        return await self.get_quote(_INDEX_UNIVERSE)

    async def get_movers(self) -> dict[str, Any]:
        """Top gainers/losers -> {"gainers": {"finance": {...}}, "losers": {...}}.

        Screener presets may need a crumb/cookie on some deployments; failures
        degrade to empty result (endpoints treat missing data as empty).
        """
        return {
            "gainers": await self._screener("most_actives"),
            "losers": await self._screener("day_losers"),
        }

    async def get_trending(self) -> dict[str, Any]:
        """Trending symbols -> {"finance": {"result": [{"quotes": [...]}]}}."""
        data = await self._get("/v1/finance/trending/US")
        return data if isinstance(data, dict) else {}

    # -- internals -----------------------------------------------------------

    async def _refresh_auth(self) -> None:
        """Fetch the Yahoo auth cookie + crumb.

        Yahoo returns 401 "Invalid Crumb" / 429 when no cookie+crumb is
        attached. ``fc.yahoo.com`` sets the cookie, ``/v1/test/getcrumb``
        (needs the cookie) returns the plain-text crumb. Both best-effort —
        on failure requests proceed without a crumb and keep their old
        degradation behavior.
        """
        self._crumb = None
        self._auth_attempted = True
        try:
            await self._client.get(
                _YAHOO_COOKIE_URL,
                headers={"Accept": "text/html", "User-Agent": _USER_AGENT},
            )
        except httpx.HTTPError:
            pass
        try:
            resp = await self._client.get(
                f"{YAHOO_BASE}{_CRUMB_PATH}", headers={"User-Agent": _USER_AGENT}
            )
            resp.raise_for_status()
            text = resp.text.strip()
            if text and len(text) < 64:
                self._crumb = text
        except httpx.HTTPError:
            self._crumb = None

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if not self._auth_attempted:
            await self._refresh_auth()
        for attempt in range(2):
            req_params = dict(params or {})
            if self._crumb:
                req_params["crumb"] = self._crumb
            try:
                resp = await self._client.get(
                    f"{YAHOO_BASE}{path}",
                    params=req_params,
                    headers={"User-Agent": _USER_AGENT},
                )
            except httpx.HTTPError as exc:
                raise YahooFinanceError(f"network error: {exc}") from exc
            if resp.status_code in _RETRYABLE_STATUS:
                if attempt == 0:
                    await self._refresh_auth()
                    continue
                if resp.status_code == 429:
                    raise YahooFinanceError("Yahoo Finance rate-limited (429)")
                raise YahooFinanceError(f"HTTP {resp.status_code}")
            if resp.status_code >= 400:
                raise YahooFinanceError(f"HTTP {resp.status_code}")
            try:
                payload: dict[str, Any] | None = resp.json()
                return payload
            except ValueError as exc:
                raise YahooFinanceError(f"invalid JSON response: {exc}") from exc
        raise YahooFinanceError("Yahoo Finance rate-limited (429)")

    async def _screener(self, preset: str) -> dict[str, Any]:
        try:
            data = await self._get(
                "/v1/finance/screener/predefined/saved",
                params={"scrIds": preset, "count": 10},
            )
        except YahooFinanceError:
            return {"finance": {"result": []}}
        return data if isinstance(data, dict) else {"finance": {"result": []}}

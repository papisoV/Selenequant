"""HTTP transport shared by venue REST clients.

Owns the httpx session, the sliding-window limiter, and the Retry-After
handshake. Subclasses supply an authenticator, a base URL, and a
``_handle_error_response`` override that maps venue error codes onto this
package's typed exceptions.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from selene_space_exchange.base.auth import BaseAuthenticator
from selene_space_exchange.base.rate_limiter import SlidingWindowRateLimiter
from selene_space_exchange.exceptions import (
    AuthenticationError,
    ExchangeAPIError,
    RateLimitExceededError,
)

logger = logging.getLogger(__name__)

_PROXY_ENV_VARS = ("SELENE_PROXY_URL",)


class BaseRESTClient:
    def __init__(
        self,
        *,
        base_url: str,
        authenticator: BaseAuthenticator,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        timeout: httpx.Timeout | float = 15.0,
        recv_window: int | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = authenticator
        self._rate_limiter = rate_limiter or SlidingWindowRateLimiter()
        self._recv_window = recv_window
        proxy = None
        for name in _PROXY_ENV_VARS:
            candidate = os.environ.get(name, "").strip()
            if candidate:
                proxy = candidate
                break
        self._http = httpx.AsyncClient(timeout=timeout, proxy=proxy)

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        signed: bool = True,
    ) -> dict[str, Any]:
        """One authenticated round trip returning parsed JSON."""
        await self._rate_limiter.acquire()

        headers: dict[str, str] = {}
        if signed:
            headers = self._auth.sign_request(method=method, path=path, params=params)
            if self._recv_window is not None:
                params = dict(params or {})
                params.setdefault("recvWindow", str(self._recv_window))

        url = f"{self._base_url}{path}"
        json_body = body if body is not None else None
        resp = await self._http.request(
            method, url, params=params or {}, json=json_body, headers=headers
        )

        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            self._rate_limiter.set_retry_after(retry_after)

        if resp.status_code >= 400:
            self._handle_error_response(resp)

        return resp.json()

    @property
    def _exchange_name(self) -> str:
        return type(self).__name__.replace("RESTClient", "").lower()

    def _handle_error_response(self, resp: httpx.Response) -> None:
        """Default mapping by HTTP status; subclasses refine per venue code."""
        try:
            payload: Any = resp.json()
        except Exception:
            payload = {"raw": resp.text}

        if resp.status_code == 429:
            raise RateLimitExceededError(
                f"{self._exchange_name} throttled us: {payload}",
                status_code=resp.status_code,
                exchange=self._exchange_name,
                details=payload if isinstance(payload, dict) else None,
            )
        if resp.status_code in (401, 403):
            raise AuthenticationError(
                f"{self._exchange_name} rejected credentials: {payload}",
                status_code=resp.status_code,
                exchange=self._exchange_name,
                details=payload if isinstance(payload, dict) else None,
            )
        raise ExchangeAPIError(
            f"{self._exchange_name} API error {resp.status_code}: {payload}",
            status_code=resp.status_code,
            exchange=self._exchange_name,
            details=payload if isinstance(payload, dict) else None,
        )

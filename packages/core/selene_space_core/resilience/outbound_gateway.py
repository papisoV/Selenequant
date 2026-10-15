"""Outbound HTTP gateway: a sync httpx.Client executed on worker threads so the
event loop never blocks, plus per-call or per-host circuit breaking.

Non-2xx responses are returned as-is (httpx semantics); stream() raises
HTTPStatusError for non-2xx since SSE consumers need a fail-fast signal.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast
from urllib.parse import urlsplit

import httpx

from selene_space_core.resilience.circuit_breaker import (
    CircuitBreaker,
)


class OutboundGateway:
    """Single entry point for external HTTP from async code."""

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        proxy: str | None = None,
        default_timeout: httpx.Timeout | float = 30.0,
        limits: httpx.Limits | None = None,
        default_circuit: bool = False,
    ) -> None:
        self._transport = transport
        self._proxy = proxy
        self._default_timeout = default_timeout
        self._limits = limits
        self._default_circuit = default_circuit
        self._circuits: dict[str, CircuitBreaker] = {}
        self._closed = False
        self._sync_client: httpx.Client | None = None

    # -- public API ---------------------------------------------------------

    @property
    def is_closed(self) -> bool:
        """httpx-style closed flag (callers across packages check this)."""
        return self._closed

    async def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        circuit: CircuitBreaker | None = None,
        timeout: httpx.Timeout | float | None = None,
    ) -> httpx.Response:
        return await self.request(
            "GET", url, params=params, headers=headers, circuit=circuit, timeout=timeout
        )

    async def post(
        self,
        url: str,
        *,
        json: object | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        content: bytes | None = None,
        circuit: CircuitBreaker | None = None,
        timeout: httpx.Timeout | float | None = None,
    ) -> httpx.Response:
        return await self.request(
            "POST",
            url,
            json=json,
            params=params,
            headers=headers,
            content=content,
            circuit=circuit,
            timeout=timeout,
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: object | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        content: bytes | None = None,
        circuit: CircuitBreaker | None = None,
        timeout: httpx.Timeout | float | None = None,
    ) -> httpx.Response:
        """Send one HTTP request through the gateway's shared client.

        Blocking I/O runs in an executor thread so the event loop never
        stalls on slow hosts. When ``circuit`` is omitted, a per-host
        breaker (if configured) guards the call.

        Args:
            method: HTTP verb.
            url: Absolute target URL; its host selects the per-host breaker.
            json: JSON-serializable request body (mutually exclusive with
                ``content`` at the httpx layer).
            params: Query-string parameters.
            headers: Extra request headers.
            content: Raw request body bytes.
            circuit: Explicit breaker override; skips the per-host one.
            timeout: Per-request override; falls back to the client default.

        Returns:
            The final :class:`httpx.Response` (status not checked — callers
            decide how to handle 4xx/5xx).

        Raises:
            CircuitOpenError: The applicable breaker is tripped.
            RuntimeError: The gateway has been closed.
        """
        self._ensure_open()
        breaker = circuit if circuit is not None else self._host_breaker(url)

        async def do_send() -> httpx.Response:
            client = self._ensure_client()
            request = client.build_request(
                method,
                url,
                json=json,
                params=params,
                headers=headers,
                content=content,
                timeout=timeout if timeout is not None else client.timeout,
            )
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, client.send, request)

        if breaker is not None:
            return cast(httpx.Response, await breaker.call(do_send))
        return await do_send()

    async def stream(
        self,
        method: str,
        url: str,
        *,
        json: object | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        circuit: CircuitBreaker | None = None,
        timeout: httpx.Timeout | float | None = None,
    ) -> AsyncIterator[str]:
        self._ensure_open()
        breaker = circuit if circuit is not None else self._host_breaker(url)

        async def open_stream() -> httpx.Response:
            client = self._ensure_client()
            request = client.build_request(
                method,
                url,
                json=json,
                params=params,
                headers=headers,
                timeout=timeout if timeout is not None else client.timeout,
            )
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, _send_streaming, client, request
            )
            if response.is_error:
                try:
                    await loop.run_in_executor(None, response.read)
                finally:
                    await loop.run_in_executor(None, response.close)
                raise httpx.HTTPStatusError(
                    f"HTTP {response.status_code} for {method} {url}",
                    request=request,
                    response=response,
                )
            return response

        if breaker is not None:
            response = await breaker.call(open_stream)
        else:
            response = await open_stream()

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _pump() -> None:
            try:
                for line in response.iter_lines():
                    loop.call_soon_threadsafe(queue.put_nowait, ("line", line))
            except BaseException as exc:  # surface pump errors to consumer
                loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        pump_task = loop.run_in_executor(None, _pump)
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "line":
                    yield payload
                elif kind == "error":
                    raise payload
                else:
                    break
            await pump_task
        finally:
            await loop.run_in_executor(None, response.close)

    async def aclose(self) -> None:
        if self._sync_client is not None and not self._sync_client.is_closed:
            await asyncio.get_running_loop().run_in_executor(
                None, self._sync_client.close
            )
        self._closed = True

    # -- internals ----------------------------------------------------------

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(
                "OutboundGateway is closed; create a new instance to issue "
                "further requests"
            )

    def _ensure_client(self) -> httpx.Client:
        if self._sync_client is None:
            kwargs: dict = {"timeout": self._default_timeout}
            if self._proxy:
                kwargs["proxy"] = self._proxy
            if self._limits:
                kwargs["limits"] = self._limits
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._sync_client = httpx.Client(**kwargs)
        return self._sync_client

    def _host_breaker(self, url: str) -> CircuitBreaker | None:
        if not self._default_circuit:
            return None
        host = urlsplit(url).hostname or ""
        existing = self._circuits.get(host)
        if existing is None:
            existing = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
            self._circuits[host] = existing
        return existing


def _send_streaming(client: httpx.Client, request: httpx.Request) -> httpx.Response:
    return client.send(request, stream=True)

"""Request-ID middleware: generation, echo-back, uniqueness, meta echo."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient

HEADER = "X-Request-ID"


@asynccontextmanager
async def health_client():
    """ASGI client bound to a fresh app, exposing only GET /health calls."""
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        yield lambda **kw: c.get("/api/v1/health", **kw)


@pytest.mark.asyncio
async def test_response_carries_request_id_header():
    async with health_client() as get:
        resp = await get()
    assert resp.status_code == 200
    assert HEADER in resp.headers
    assert len(resp.headers[HEADER]) > 0


@pytest.mark.asyncio
async def test_client_supplied_id_is_echoed():
    custom_id = "my-custom-request-id-12345"
    async with health_client() as get:
        resp = await get(headers={HEADER: custom_id})
    assert resp.headers[HEADER] == custom_id


@pytest.mark.asyncio
async def test_two_requests_get_different_ids():
    async with health_client() as get:
        id1 = (await get()).headers[HEADER]
        id2 = (await get()).headers[HEADER]
    assert id1 != id2


@pytest.mark.asyncio
async def test_body_meta_matches_header_id():
    async with health_client() as get:
        resp = await get()
    assert HEADER in resp.headers
    assert resp.json()["meta"]["request_id"] == resp.headers[HEADER]

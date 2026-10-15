"""Rate-limit middleware: gated on billing mode.

The limiter is per-IP at 120 req/60s — sized for a shared/remote deployment,
where it is a real guard. A local run binds 127.0.0.1 and fans out ~91
requests per page load, so the same quota there only throttles the one user
who has no alternative route in. These tests pin both halves: local passes,
billing mode still blocks.

Reverse-verify: drop the `if billing_mode_enabled():` guard and
test_local_mode_does_not_throttle goes red.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient

# One past the 120/60s global quota, so a live limiter must produce 429s.
REQUESTS = 130


@asynccontextmanager
async def health_client():
    """ASGI client bound to a fresh app, issuing GET /api/v1/health."""
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        yield lambda: c.get("/api/v1/health")


async def _status_codes(get) -> list[int]:
    return [(await get()).status_code for _ in range(REQUESTS)]


@pytest.mark.asyncio
async def test_local_mode_does_not_throttle(monkeypatch):
    """Without billing mode the limiter is not mounted — 130 calls all pass."""
    monkeypatch.delenv("SELENE_BILLING_MODE", raising=False)

    async with health_client() as get:
        codes = await _status_codes(get)

    assert codes.count(429) == 0, f"local run throttled itself: {codes.count(429)}x 429"
    assert all(c == 200 for c in codes), f"unexpected non-200: {sorted(set(codes))}"


@pytest.mark.asyncio
async def test_billing_mode_still_throttles(monkeypatch):
    """Billing mode (shared deployment) keeps the guard: over quota → 429."""
    monkeypatch.setenv("SELENE_BILLING_MODE", "1")
    monkeypatch.setenv("SELENE_JWT_SECRET", "test-secret-for-rate-limit-gate")

    # ASGITransport reports client host "testclient"; every call shares one
    # bucket, which is exactly how the limiter behaves behind a reverse proxy.
    async with health_client() as get:
        codes = await _status_codes(get)

    assert 429 in codes, "billing mode lost its rate limit"
    # The quota is per-window, so the first 120 must get through untouched.
    assert codes[:120].count(429) == 0, "limiter cut in before the quota"

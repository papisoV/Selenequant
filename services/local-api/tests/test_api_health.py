"""Health endpoint aggregation: healthy / degraded / unhealthy roll-ups."""

from __future__ import annotations

import pytest
from app.core.dependencies import get_health_registry
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_core.diagnostics.health import HealthCheckResult, HealthRegistry


def _app(registry: HealthRegistry | None = None):
    app = create_app()
    if registry is not None:
        app.dependency_overrides[get_health_registry] = lambda: registry
    return app


def _registry_with(*checks) -> HealthRegistry:
    """checks: (name, status_or_exception) tuples."""
    reg = HealthRegistry()
    for spec in checks:
        reg.register_check(spec[0], _run_check(spec))
    return reg


def _run_check(spec):
    name, outcome = spec

    async def check():
        if isinstance(outcome, Exception):
            raise outcome
        return HealthCheckResult(name=name, status=outcome, latency_ms=0)

    return check


async def _fetch_health(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/health")
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.asyncio
async def test_local_api_component_self_reports():
    body = await _fetch_health(create_app())
    assert "local_api" in body["data"]["components"]
    assert body["data"]["components"]["local_api"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_empty_registry_still_healthy():
    body = await _fetch_health(_app(HealthRegistry()))
    assert body["data"]["status"] == "healthy"
    assert body["data"]["version"] == "0.1.0"
    assert body["data"]["components"] == {}
    assert body["error"] is None


@pytest.mark.asyncio
async def test_single_passing_check_shows_in_components():
    body = await _fetch_health(_app(_registry_with(("db", "healthy"))))
    assert body["data"]["status"] == "healthy"
    assert "db" in body["data"]["components"]
    assert body["data"]["components"]["db"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_degraded_check_downgrades_overall_status():
    body = await _fetch_health(
        _app(_registry_with(("ws", "healthy"), ("llm", "degraded")))
    )
    assert body["data"]["status"] == "degraded"


@pytest.mark.asyncio
async def test_raising_check_marks_registry_unhealthy():
    body = await _fetch_health(
        _app(_registry_with(("exchange_ws", RuntimeError("connection lost"))))
    )
    assert body["data"]["status"] == "unhealthy"
    assert body["data"]["components"]["exchange_ws"]["status"] == "unhealthy"

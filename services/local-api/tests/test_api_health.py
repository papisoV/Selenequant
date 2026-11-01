"""Health endpoint aggregation: healthy / degraded / unhealthy roll-ups."""

from __future__ import annotations

import pytest
from app.core.dependencies import get_health_registry
from app.main import create_app, market_health_verdict
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


async def _fetch_ready(app):
    """单独一条: 就绪探针会故意返 503,不能复用 _fetch_health 里写死的 200 断言 ——
    那样 503 的用例会在取数前就挂掉,或者反过来永远测不到非 200。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.get("/api/v1/health/ready")


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


# ---------------------------------------------------------------------------
# 就绪探针 (/health/ready): 可服务性 ≠ 进程存活
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ready_is_200_when_every_component_healthy():
    resp = await _fetch_ready(_app(_registry_with(("db", "healthy"))))
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["ready"] is True
    assert body["data"]["status"] == "healthy"
    assert body["error"] is None


@pytest.mark.asyncio
async def test_ready_is_503_when_degraded():
    resp = await _fetch_ready(
        _app(_registry_with(("ws", "healthy"), ("llm", "degraded")))
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["data"]["ready"] is False
    assert body["data"]["status"] == "degraded"
    # 信封在非 200 上也必须是同一个形状,否则 probe 与调试侧看到两套
    assert body["error"] is None
    assert "components" in body["data"]


@pytest.mark.asyncio
async def test_ready_is_503_when_unhealthy():
    resp = await _fetch_ready(
        _app(_registry_with(("exchange_ws", RuntimeError("connection lost"))))
    )
    assert resp.status_code == 503
    assert resp.json()["data"]["ready"] is False


@pytest.mark.asyncio
async def test_health_stays_200_while_ready_is_503():
    """这是两层分工的核心: /health 是前端启动门,降级时仍须 200。"""
    app = _app(_registry_with(("llm", "degraded")))
    assert (await _fetch_health(app))["data"]["status"] == "degraded"
    assert (await _fetch_ready(app)).status_code == 503


# ---------------------------------------------------------------------------
# market_data 判定: 服务活着 ≠ 行情源活着
# ---------------------------------------------------------------------------


def test_market_health_degraded_when_service_up_but_every_source_down():
    """B1 的承重点: start() 在所有源都启动失败时仍置 _running=True。"""
    status, any_running = market_health_verdict(
        True,
        {
            "binance/spot": {"running": False, "symbols": 0},
            "okx/spot": {"running": False, "symbols": 0},
        },
    )
    assert status == "degraded"
    assert any_running is False


def test_market_health_healthy_when_at_least_one_source_running():
    status, any_running = market_health_verdict(
        True,
        {
            "binance/spot": {"running": False, "symbols": 3},
            "okx/spot": {"running": True, "symbols": 3},
        },
    )
    assert status == "healthy"
    assert any_running is True


def test_market_health_healthy_when_no_sources_registered():
    """注册空集不据此降级: 纯静态数据的部署无从判断,别误报。"""
    status, any_running = market_health_verdict(True, {})
    assert status == "healthy"
    assert any_running is False


def test_market_health_degraded_when_service_not_running():
    status, _ = market_health_verdict(False, {"okx/spot": {"running": True}})
    assert status == "degraded"

"""Enhanced health check endpoint.

Returns system status with per-component health information
sourced from the ``HealthRegistry`` in ``selene_space_core``.

Public endpoint returns only aggregate status; detailed component
information is restricted to localhost / internal requests.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.core.dependencies import build_response_meta, get_health_registry
from selene_space_core.diagnostics.health import HealthRegistry
from selene_space_types.common import APIResponse

router = APIRouter(tags=["health"])

_IS_PRODUCTION = os.environ.get("SELENE_ENV", "").lower() == "production"
#: 视为内网的来源(直连 TCP 地址, 不看 x-forwarded-for)。
_INTERNAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_VERSION = "0.1.0"
#: 组件状态优先级: 取最差的一个当前缀。
_SEVERITY = ("unhealthy", "degraded")


def _aggregate_status(components: dict[str, Any]) -> str:
    """Derive overall status from component statuses."""
    statuses = {c.get("status", "healthy") for c in components.values()}
    for level in _SEVERITY:
        if level in statuses:
            return level
    return "healthy"


def _is_internal_request(request: Request) -> bool:
    """Return True if the request originates from localhost.

    Does NOT trust x-forwarded-for for security decisions.
    Only the direct client IP (from the TCP connection) is trusted.
    """
    client = request.client
    return client is not None and client.host in _INTERNAL_HOSTS


def _component_entry(result: Any) -> dict[str, Any]:
    """单个组件转成响应条目; 有 detail 才带上。"""
    entry: dict[str, Any] = {
        "status": result.status,
        "latency_ms": round(result.latency_ms, 2),
    }
    if result.detail:
        entry["detail"] = result.detail
    return entry


def _component_entries(results: Any) -> dict[str, Any]:
    """把 registry 的结果逐条转成响应里的组件条目。"""
    return {r.name: _component_entry(r) for r in results}


def _response(request: Request, data: dict[str, Any]) -> APIResponse[dict[str, Any]]:
    """统一收尾: 挂上响应元信息、无错误。"""
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


@router.get("/health")
async def health_check(
    request: Request,
    registry: HealthRegistry = Depends(get_health_registry),
) -> APIResponse[dict[str, Any]]:
    """Return service health status.

    In production, public requests receive only aggregate status.
    Internal/localhost requests receive full component breakdown.
    """
    components = _component_entries(await registry.run_all())
    overall = _aggregate_status(components)

    if _IS_PRODUCTION and not _is_internal_request(request):
        return _response(request, {"status": overall, "version": _VERSION})

    return _response(
        request,
        {"status": overall, "version": _VERSION, "components": components},
    )


@router.get("/health/ready")
async def health_ready(
    request: Request,
    response: Response,
    registry: HealthRegistry = Depends(get_health_registry),
) -> APIResponse[dict[str, Any]]:
    """Readiness probe: 503 unless every component is healthy.

    ``/health`` 是前端启动门 —— 组件再坏它也返 200,否则前端 60 次重试耗尽直接
    白屏。容器编排要的恰恰相反:坏到不能服务就该被摘掉。故两件事分开:
    存活看 ``/health``,可服务看这里。

    状态码走注入的 ``Response`` 而不是 ``HTTPException`` —— 后者会丢掉
    统一信封(``{data, meta, error}``),probe 侧与调试侧看到的就是两套形状。
    """
    components = _component_entries(await registry.run_all())
    overall = _aggregate_status(components)
    ready = overall == "healthy"
    if not ready:
        response.status_code = 503

    if _IS_PRODUCTION and not _is_internal_request(request):
        # 公网只给「能不能服务」,不外泄哪些组件坏了
        return _response(
            request, {"status": overall, "ready": ready, "version": _VERSION}
        )

    return _response(
        request,
        {
            "status": overall,
            "ready": ready,
            "version": _VERSION,
            "components": components,
        },
    )

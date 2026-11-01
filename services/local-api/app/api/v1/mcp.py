"""MCP server management endpoints.

Provides CRUD operations for MCP server configurations and status inspection.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.dependencies import AuthenticatedUser, get_mcp_registry, optional_user

router = APIRouter(prefix="/mcp", tags=["mcp"])

_RISK_LEVELS = {
    "safe": "SAFE",
    "restricted": "RESTRICTED",
    "dangerous": "DANGEROUS",
}


def _user_prefix(user: AuthenticatedUser, name: str) -> str:
    """Namespace MCP server names by user_id when multi-user auth is enabled."""
    return name


def _strip_prefix(user: AuthenticatedUser, prefixed: str) -> str | None:
    """Strip user prefix and return original name, or None if not owned."""
    return prefixed


def _status_payload(name: str, status: Any) -> dict[str, Any]:
    """Common {name, connected, tool_count, error} shape for server mutations."""
    return {
        "name": name,
        "connected": status.connected,
        "tool_count": status.tool_count,
        "error": status.error,
    }


def _ensure_registry(registry: Any, *, as_list: bool) -> Any:
    """Raise 503 for mutations when the registry is absent; return it otherwise.

    For list endpoints callers pass ``as_list=True`` and get the sentinel
    ``None`` back instead of an exception (empty list is a valid answer).
    """
    if registry is None and not as_list:
        raise HTTPException(503, "MCP registry not initialized")
    return registry


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class McpServerCreateRequest(BaseModel):
    """Request body for adding a new MCP server."""

    command: str | None = Field(None, description="Command to run (stdio)")
    args: list[str] = Field(default_factory=list, description="Command arguments")
    env: dict[str, str] = Field(
        default_factory=dict, description="Environment variables"
    )
    cwd: str | None = Field(None, description="Working directory")
    url: str | None = Field(None, description="Server URL (SSE)")
    transport: str = Field("stdio", description="Transport type: stdio or sse")
    enabled: bool = Field(True, description="Whether the server is active")
    risk_level: str = Field("restricted", description="Default risk level for tools")


class McpServerResponse(BaseModel):
    """Response model for an MCP server status."""

    name: str
    connected: bool = False
    tool_count: int = 0
    error: str | None = None
    transport: str = "stdio"
    tools: list[dict[str, Any]] = Field(default_factory=list)


class McpToolResponse(BaseModel):
    """Response model for an MCP tool."""

    server_name: str
    tool_name: str
    registered_name: str
    description: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/servers")
async def list_mcp_servers(
    registry: Any = Depends(get_mcp_registry),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """List MCP servers owned by the current user."""
    if registry is None:
        return {"servers": [], "message": "MCP registry not initialized"}

    owned: list[dict[str, Any]] = []
    for server in registry.list_servers():
        display_name = _strip_prefix(user, server.name)
        if display_name is None:
            continue
        owned.append(
            {
                "name": display_name,
                "connected": server.connected,
                "tool_count": server.tool_count,
                "error": server.error,
                "transport": server.config.transport.value
                if hasattr(server.config, "transport")
                else "stdio",
                "tools": [
                    {
                        "server_name": display_name,
                        "tool_name": tool.tool_name,
                        "description": tool.description,
                    }
                    for tool in server.tools
                ],
            }
        )
    return {"servers": owned}


@router.post("/servers/{name}")
async def add_mcp_server(
    name: str,
    body: McpServerCreateRequest,
    registry: Any = Depends(get_mcp_registry),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Add and connect a new MCP server (scoped to current user)."""
    _ensure_registry(registry, as_list=False)

    from selene_space_agent.mcp.types import McpServerConfig, McpTransport
    from selene_space_types.risk import RiskLevel

    transport = (
        McpTransport(body.transport)
        if body.transport in ("stdio", "sse")
        else McpTransport.STDIO
    )
    risk_level = getattr(RiskLevel, _RISK_LEVELS.get(body.risk_level, "RESTRICTED"))

    status = await registry.add_server(
        _user_prefix(user, name),
        McpServerConfig(
            command=body.command,
            args=body.args,
            env=body.env,
            cwd=body.cwd,
            url=body.url,
            transport=transport,
            enabled=body.enabled,
            risk_level=risk_level,
        ),
    )
    return _status_payload(name, status)


@router.delete("/servers/{name}")
async def remove_mcp_server(
    name: str,
    registry: Any = Depends(get_mcp_registry),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, str]:
    """Remove an MCP server (must be owned by current user)."""
    _ensure_registry(registry, as_list=False)
    await registry.remove_server(_user_prefix(user, name))
    return {"status": "removed", "name": name}


@router.post("/servers/{name}/refresh")
async def refresh_mcp_server(
    name: str,
    registry: Any = Depends(get_mcp_registry),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Reconnect to an MCP server (must be owned by current user)."""
    _ensure_registry(registry, as_list=False)
    status = await registry.refresh_server(_user_prefix(user, name))
    return _status_payload(name, status)


@router.get("/tools")
async def list_mcp_tools(
    registry: Any = Depends(get_mcp_registry),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """List tools from MCP servers owned by the current user."""
    if registry is None:
        return {"tools": []}

    tools: list[dict[str, Any]] = []
    for tool in registry.list_mcp_tools():
        display_name = _strip_prefix(user, tool.server_name)
        if display_name is None:
            continue
        tools.append(
            {
                "server_name": display_name,
                "tool_name": tool.tool_name,
                "registered_name": f"mcp_{display_name}_{tool.tool_name}",
                "description": tool.description,
            }
        )
    return {"tools": tools}

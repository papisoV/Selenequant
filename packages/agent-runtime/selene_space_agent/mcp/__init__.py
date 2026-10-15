"""MCP (Model Context Protocol) module for SeleneQuant agent runtime.

Two directions:

**Client** (existing) — SeleneQuant connects to user-configured *external* MCP
servers.  Each server's tools are wrapped as ``BaseTool`` subclasses and
registered in the shared ``ToolCatalog``.

**Server** (``server`` submodule) — SeleneQuant *exposes* a curated read-only
subset of its own tools to external MCP clients (Claude Code, Codex, …), reusing
the same ``ToolCatalog``.  Mounted at ``/mcp-server``.

Public API:
    - ``McpConfig`` / ``McpServerConfig`` — configuration models
    - ``McpTransport`` — stdio / SSE transport enum
    - ``McpToolInfo`` — tool metadata discovered from a server
    - ``McpServerStatus`` — runtime status of a server connection
    - ``McpClientSession`` — manages a single MCP server connection
    - ``McpToolWrapper`` — adapts an MCP tool to ``BaseTool``
    - ``McpRegistry`` — manages all MCP connections and tool registrations
    - ``server.build_subapp`` / ``server.register_tools`` — the outward-facing
      server (see ``selene_space_agent.mcp.server``)
"""

from __future__ import annotations

# Lazy imports for classes that depend on the optional `mcp` SDK.
# We re-export them here so callers can do:
#     from selene_space_agent.mcp import McpClientSession, McpToolWrapper, McpRegistry
from selene_space_agent.mcp.client import McpClientSession
from selene_space_agent.mcp.registry import McpRegistry
from selene_space_agent.mcp.tool_wrapper import McpToolWrapper
from selene_space_agent.mcp.types import (
    McpConfig,
    McpServerConfig,
    McpServerStatus,
    McpToolInfo,
    McpToolResult,
    McpTransport,
)

__all__ = [
    "McpConfig",
    "McpClientSession",
    "McpRegistry",
    "McpServerConfig",
    "McpServerStatus",
    "McpToolInfo",
    "McpToolResult",
    "McpToolWrapper",
    "McpTransport",
]

"""Data models shared by the MCP client, registry and tool wrapper."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel

from selene_space_types.risk import RiskLevel


class McpTransport(str, Enum):
    """Wire format used to talk to a server: subprocess or HTTP stream."""

    STDIO = "stdio"
    SSE = "sse"


def _missing_for(transport: "McpServerConfig") -> list[str]:
    problems: list[str] = []
    uses_subprocess = transport.transport == McpTransport.STDIO
    uses_http = transport.transport == McpTransport.SSE
    if uses_subprocess and not transport.command:
        problems.append("stdio transport requires 'command' field")
    if uses_http and not transport.url:
        problems.append("SSE transport requires 'url' field")
    return problems


class McpServerConfig(BaseModel):
    """Everything needed to reach one server: launch spec plus policy tag."""

    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    cwd: str | None = None
    url: str | None = None
    transport: McpTransport = McpTransport.STDIO
    enabled: bool = True
    risk_level: RiskLevel = RiskLevel.RESTRICTED

    def validate_config(self) -> list[str]:
        """List configuration problems; an empty list means usable."""
        return _missing_for(self)


class McpConfig(BaseModel):
    """Named collection of server configs, mirroring the YAML layout."""

    servers: dict[str, McpServerConfig] = {}


class McpToolInfo(BaseModel):
    """One tool advertised by a remote server."""

    server_name: str
    tool_name: str
    description: str = ""
    input_schema: dict[str, Any] = {}


class McpToolResult(BaseModel):
    """Text outcome of one remote invocation."""

    content: str = ""
    is_error: bool = False


class McpServerStatus(BaseModel):
    """Live view of one connection: reachability plus discovered tools."""

    name: str
    config: McpServerConfig
    connected: bool = False
    tool_count: int = 0
    error: str | None = None
    tools: list[McpToolInfo] = []

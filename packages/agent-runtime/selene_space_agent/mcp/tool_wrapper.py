"""Adapt remote MCP tools to the local BaseTool interface."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from selene_space_agent.mcp.types import McpToolInfo, McpToolResult
from selene_space_types.risk import RiskLevel

_SANITIZE_RE = re.compile(r"[^a-z0-9]")


def _sanitize_name(raw: str) -> str:
    lowered = raw.lower()
    replaced = _SANITIZE_RE.sub("_", lowered)
    return replaced.strip("_")


class McpToolWrapper:
    """Expose one remote MCP tool as a locally callable tool object."""

    def __init__(
        self,
        session: Any,
        tool_info: McpToolInfo,
        risk_level: RiskLevel = RiskLevel.RESTRICTED,
    ) -> None:
        self._session = session
        self._tool_info = tool_info
        self.risk_level = risk_level

    @property
    def name(self) -> str:
        server = _sanitize_name(self._tool_info.server_name)
        tool = _sanitize_name(self._tool_info.tool_name)
        return f"mcp_{server}_{tool}"

    @property
    def description(self) -> str:
        base = self._tool_info.description or (
            f"Remote tool '{self._tool_info.tool_name}' from MCP server "
            f"'{self._tool_info.server_name}'."
        )
        return f"[MCP: {self._tool_info.server_name}] {base}"

    @property
    def parameters(self) -> dict[str, Any]:
        schema = self._tool_info.input_schema
        if not schema:
            return {"type": "object", "properties": {}}
        return schema

    def execute(self, arguments: dict[str, Any] | None = None) -> Any:
        try:
            result: McpToolResult = asyncio.run(
                self._session.call_tool(self._tool_info.tool_name, arguments or {})
            )
        except Exception as exc:
            from selene_space_agent.tools.base import ToolResult

            return ToolResult(
                output="",
                error=f"MCP tool execution failed: {exc}",
            )

        from selene_space_agent.tools.base import ToolResult

        if result.is_error:
            return ToolResult(output=result.content, error=result.content)
        return ToolResult(output=result.content, error=None)

    def to_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "risk_level": self.risk_level.value
            if hasattr(self.risk_level, "value")
            else self.risk_level,
        }

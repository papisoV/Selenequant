"""Async MCP client session over stdio or SSE transport."""

from __future__ import annotations

import logging
from typing import Any

from selene_space_agent.mcp.types import McpToolInfo, McpToolResult

logger = logging.getLogger(__name__)


class McpClientError(Exception):
    """Raised when an MCP session cannot be established or used."""


class McpClientSession:
    """Owns one connection to an MCP server.

    The ``mcp`` SDK is imported lazily inside :meth:`connect` so importing
    this module never pulls the SDK (or any transitive dependency) in.
    """

    def __init__(self, server_name: str, config) -> None:
        self.server_name = server_name
        self.config = config
        self._session: Any | None = None
        self._tools: list[McpToolInfo] = []

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    async def connect(self) -> None:
        if self._session is not None:
            return
        try:
            pass
        except Exception as exc:
            raise McpClientError(
                f"MCP SDK unavailable for server '{self.server_name}': {exc}"
            ) from exc

        try:
            if self.config.transport == "sse" or str(self.config.transport) == "sse":
                session = await self._connect_sse()
            else:
                session = await self._connect_stdio()
        except McpClientError:
            raise
        except Exception as exc:
            raise McpClientError(
                f"failed to connect to MCP server '{self.server_name}': {exc}"
            ) from exc
        self._session = session

    async def _connect_sse(self) -> Any:
        from mcp.client.sse import sse_client

        read, write = await sse_client(self.config.url).__aenter__()
        from mcp import ClientSession

        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        return session

    async def _connect_stdio(self) -> Any:
        from mcp import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=self.config.command,
            args=list(self.config.args or []),
            env={**self.config.env} if self.config.env else None,
            cwd=self.config.cwd,
        )
        read, write = await stdio_client(params).__aenter__()
        from mcp import ClientSession

        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        return session

    async def disconnect(self) -> None:
        self._session = None
        self._tools = []

    async def list_tools(self) -> list[McpToolInfo]:
        if self._session is None:
            raise McpClientError(f"server '{self.server_name}' is not connected")
        response = await self._session.list_tools()
        tools: list[McpToolInfo] = []
        for tool in getattr(response, "tools", []) or []:
            tools.append(
                McpToolInfo(
                    server_name=self.server_name,
                    tool_name=getattr(tool, "name", "") or "",
                    description=getattr(tool, "description", "") or "",
                    input_schema=getattr(tool, "inputSchema", None) or {},
                )
            )
        self._tools = tools
        return tools

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> McpToolResult:
        if self._session is None:
            raise McpClientError(f"server '{self.server_name}' is not connected")
        result = await self._session.call_tool(tool_name, arguments or {})
        content_parts = getattr(result, "content", []) or []
        text_chunks = [
            getattr(part, "text", "")
            for part in content_parts
            if getattr(part, "type", None) == "text" or hasattr(part, "text")
        ]
        content = "".join(text_chunks)
        return McpToolResult(
            content=content,
            is_error=bool(getattr(result, "isError", False)),
        )

    async def health_check(self) -> bool:
        try:
            if self._session is None:
                return False
            await self._session.list_tools()
            return True
        except Exception:
            logger.warning("health check failed for '%s'", self.server_name)
            return False

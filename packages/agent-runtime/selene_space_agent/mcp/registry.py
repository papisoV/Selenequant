"""McpRegistry — lifecycle and status tracking for configured MCP servers."""

from __future__ import annotations

import logging
from typing import Any

from selene_space_agent.mcp.client import McpClientSession
from selene_space_agent.mcp.tool_wrapper import McpToolWrapper
from selene_space_agent.mcp.types import McpConfig, McpServerStatus, McpToolInfo

logger = logging.getLogger(__name__)


class McpRegistry:
    """Connect configured MCP servers and expose their tools in a catalog."""

    def __init__(self) -> None:
        self._config = McpConfig()
        self._sessions: dict[str, McpClientSession] = {}
        self._statuses: dict[str, McpServerStatus] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self, mcp_config: McpConfig | None, tool_catalog: Any = None
    ) -> None:
        """Load config, then connect every enabled server and wrap its tools."""
        if mcp_config is not None:
            self._config = mcp_config

        for name, server_config in self._config.servers.items():
            if not server_config.enabled:
                continue
            await self._connect_one(name, server_config, tool_catalog)

    async def _connect_one(self, name: str, server_config, tool_catalog) -> None:
        errors = server_config.validate_config()
        if errors:
            self._statuses[name] = McpServerStatus(
                name=name,
                config=server_config,
                connected=False,
                error="; ".join(errors),
            )
            return

        session = McpClientSession(name, server_config)
        try:
            await session.connect()
            tools = await session.list_tools()
        except Exception as exc:
            logger.warning("failed to start MCP server '%s': %s", name, exc)
            self._statuses[name] = McpServerStatus(
                name=name,
                config=server_config,
                connected=False,
                error=str(exc),
            )
            return

        self._sessions[name] = session
        self._statuses[name] = McpServerStatus(
            name=name,
            config=server_config,
            connected=True,
            tool_count=len(tools),
            tools=tools,
        )
        if tool_catalog is not None:
            self._register_tools(tools, session, server_config.risk_level, tool_catalog)

    def _register_tools(
        self, tools: list[McpToolInfo], session, risk_level, tool_catalog
    ) -> None:
        for info in tools:
            wrapper = McpToolWrapper(
                session=session,
                tool_info=info,
                risk_level=risk_level,
            )
            try:
                tool_catalog.register(wrapper)
            except KeyError:
                logger.info(
                    "skipping MCP tool '%s' — name already registered", wrapper.name
                )

    async def stop(self) -> None:
        """Disconnect every active server session."""
        for name in list(self._sessions):
            session = self._sessions.pop(name, None)
            if session is None:
                continue
            try:
                await session.disconnect()
            except Exception:
                logger.warning("error disconnecting '%s'", name)
            status = self._statuses.get(name)
            if status is not None:
                self._statuses[name] = status.model_copy(update={"connected": False})

    async def add_server(self, name: str, server_config) -> McpServerStatus:
        """Register (replacing any existing entry) and connect one server."""
        old_session = self._sessions.pop(name, None)
        if old_session is not None:
            try:
                await old_session.disconnect()
            except Exception:
                pass

        self._config.servers[name] = server_config
        if not server_config.enabled:
            self._statuses[name] = McpServerStatus(
                name=name, config=server_config, connected=False
            )
            return self._statuses[name]

        await self._connect_one(name, server_config, None)
        return self._statuses.setdefault(
            name,
            McpServerStatus(name=name, config=server_config, connected=False),
        )

    async def remove_server(self, name: str) -> bool:
        session = self._sessions.pop(name, None)
        if session is not None:
            try:
                await session.disconnect()
            except Exception:
                pass
        self._statuses.pop(name, None)
        return self._config.servers.pop(name, None) is not None

    async def refresh_server(self, name: str) -> McpServerStatus:
        server_config = self._config.servers.get(name)
        if server_config is None:
            raise KeyError(f"MCP server '{name}' is not configured")

        old_session = self._sessions.pop(name, None)
        if old_session is not None:
            try:
                await old_session.disconnect()
            except Exception:
                pass

        await self._connect_one(name, server_config, None)
        return self._statuses.setdefault(
            name,
            McpServerStatus(name=name, config=server_config, connected=False),
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_servers(self) -> list[McpServerStatus]:
        names = sorted(set(self._statuses) | set(self._config.servers))
        statuses: list[McpServerStatus] = []
        for name in names:
            if name in self._statuses:
                statuses.append(self._statuses[name])
                continue
            statuses.append(
                McpServerStatus(
                    name=name,
                    config=self._config.servers[name],
                    connected=False,
                )
            )
        return statuses

    def get_status(self, name: str) -> McpServerStatus | None:
        return self._statuses.get(name)

    def list_mcp_tools(self) -> list[McpToolInfo]:
        """Flat list of tools discovered across all connected servers."""
        tools: list[McpToolInfo] = []
        for name in sorted(self._sessions):
            status = self._statuses.get(name)
            if status is None:
                continue
            tools.extend(status.tools)
        return tools

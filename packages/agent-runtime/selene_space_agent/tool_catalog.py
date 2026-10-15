"""Catalog of callable tools with policy-gated visibility."""

from __future__ import annotations

from typing import Any

from selene_space_agent.tools.base import BaseTool, strip_confirm_fields
from selene_space_types.risk import RiskLevel


class ToolCatalogError(Exception):
    """Signal that a tool could not be admitted to the catalog."""


def _by_name(tool: BaseTool) -> str:
    return tool.name


class ToolCatalog:
    """Single home for the tools an agent may call.

    When a ``policy_engine`` is supplied (the security-gateway's
    ``ToolPolicyEngine``), only tools it approves are surfaced to the
    model; otherwise nothing is filtered.
    """

    def __init__(self, policy_engine: Any | None = None) -> None:
        self._policy_engine = policy_engine
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Admit ``tool`` under its canonical name; duplicate names abort."""
        if self._tools.get(tool.name) is not None or tool.name in self._tools:
            raise ToolCatalogError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = tool

    def register_many(self, tools: list[BaseTool]) -> None:
        """Admit every tool in ``tools``, stopping at the first failure."""
        for item in tools:
            self.register(item)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def is_tool_allowed(self, name: str) -> bool:
        if self._policy_engine is None:
            return True
        return bool(self._policy_engine.is_tool_allowed(name))

    def tool_names(self) -> list[str]:
        return sorted(self._tools.keys())

    def list_tools(self, risk_level: RiskLevel | None = None) -> list[BaseTool]:
        selected = [
            t
            for t in self._tools.values()
            if risk_level is None or t.risk_level == risk_level
        ]
        return sorted(selected, key=_by_name)

    def list_allowed_tools(self) -> list[BaseTool]:
        visible = [t for t in self._tools.values() if self.is_tool_allowed(t.name)]
        return sorted(visible, key=_by_name)

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Expose tool schemas to the model with confirmation fields removed.

        Any consumer of this catalog (the react runtime, the MCP server)
        inherits the strip, so the model can never see ``user_confirmed``
        and therefore can never forge it.
        """
        return [
            strip_confirm_fields(item.to_definition())
            for item in self.list_allowed_tools()
        ]

    def __len__(self) -> int:
        return len(self._tools)

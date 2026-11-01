"""Agent runtime package for SeleneQuant quantitative research.

Re-exports the conversation loop, tool catalog and prompt helpers that
callers throughout the platform rely on::

    from selene_space_agent import AgentRuntime, ToolCatalog
"""

from selene_space_agent.context.manager import (
    ContextManager as _ContextManager,
)
from selene_space_agent.prompt_builder import (
    AgentContext as _AgentContext,
    build_system_prompt as _build_system_prompt,
)
from selene_space_agent.runtime import AgentRuntime as _AgentRuntime
from selene_space_agent.tool_catalog import ToolCatalog as _ToolCatalog
from selene_space_agent.tools.base import (
    BaseTool as _BaseTool,
    ToolResult as _ToolResult,
)

AgentContext = _AgentContext
AgentRuntime = _AgentRuntime
BaseTool = _BaseTool
ContextManager = _ContextManager
ToolCatalog = _ToolCatalog
ToolResult = _ToolResult
build_system_prompt = _build_system_prompt

__all__ = [
    "AgentContext",
    "AgentRuntime",
    "BaseTool",
    "ContextManager",
    "ToolCatalog",
    "ToolResult",
    "build_system_prompt",
]

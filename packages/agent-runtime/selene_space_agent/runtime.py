"""Legacy agent runtime — JSON-in-text protocol, kept for compatibility.

The modern loop lives in ``react.py``; at import time this module aliases
``AgentRuntime`` to ``ReActAgentRuntime`` so consumers of the old name get
the current engine.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from selene_space_agent.context.manager import ContextManager
from selene_space_agent.events import make_event
from selene_space_agent.prompt_builder import AgentContext, build_system_prompt
from selene_space_agent.tool_catalog import ToolCatalog
from selene_space_types.agent import AgentStreamEvent, AgentStreamEventType


@runtime_checkable
class LLMProviderProtocol(Protocol):
    """Structural type matching the LLM provider surface we rely on."""

    async def chat(self, messages: list[Any], **kwargs: Any) -> str: ...

    async def chat_stream(
        self, messages: list[Any], **kwargs: Any
    ) -> AsyncIterator[str]: ...

    async def chat_with_tools(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any: ...

    async def generate_structured(
        self,
        messages: list[Any],
        output_schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]: ...


class LegacyAgentRuntime:
    """Drive tool calls by parsing JSON blobs out of plain chat completions."""

    def __init__(
        self,
        llm: LLMProviderProtocol,
        tool_catalog: ToolCatalog,
        context_manager: ContextManager,
        prompt_context: AgentContext,
        max_tool_rounds: int = 8,
    ) -> None:
        self._llm = llm
        self._catalog = tool_catalog
        self._context = context_manager
        self._prompt_context = prompt_context
        self._max_tool_rounds = max_tool_rounds

    def _build_messages(self) -> list[dict[str, str]]:
        system_prompt = build_system_prompt(self._prompt_context)
        return [{"role": "system", "content": system_prompt}] + [
            {"role": m.role, "content": m.content} for m in self._context.get_messages()
        ]

    def _parse_response(self, raw: str) -> tuple[str, list[dict[str, Any]]]:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw.strip(), []
        if not isinstance(payload, dict):
            return str(payload), []
        text = payload.get("response") or ""
        calls = [c for c in payload.get("tool_calls", []) or [] if isinstance(c, dict)]
        return text, calls

    def _execute_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> tuple[str, str | None]:
        tool = self._catalog.get(name)
        if tool is None:
            return "", f"Tool '{name}' not found"
        if not self._catalog.is_tool_allowed(name):
            return "", f"Tool '{name}' blocked by policy"
        result = tool.execute(arguments)
        content = self._context.truncate_tool_result(result.output or "")
        return content, result.error

    async def process_message(self, message: str) -> AsyncIterator[AgentStreamEvent]:
        self._context.add_message("user", message)

        for _round in range(self._max_tool_rounds):
            messages = self._build_messages()
            raw = await self._llm.chat(messages)
            response_text, tool_calls = self._parse_response(raw)

            if not tool_calls:
                answer = response_text.strip() or "No more responses configured."
                yield make_event(AgentStreamEventType.TEXT_DELTA, {"text": answer})
                self._context.add_message("assistant", answer)
                break

            for call in tool_calls:
                name = call.get("tool") or call.get("name") or ""
                arguments = call.get("arguments") or {}
                yield make_event(
                    AgentStreamEventType.TOOL_CALL,
                    {"name": name, "arguments": arguments},
                )
                output, error = self._execute_tool(name, arguments)
                event_data: dict[str, Any] = {"name": name, "output": output}
                if error:
                    event_data["error"] = error
                    yield make_event(AgentStreamEventType.TOOL_RESULT, event_data)
                    self._context.add_message(
                        "user",
                        f"Tool {name} failed: {error}",
                    )
                else:
                    yield make_event(AgentStreamEventType.TOOL_RESULT, event_data)
                    self._context.add_message("user", f"Tool {name} result: {output}")
        else:
            notice = "Reached maximum tool rounds without a final answer."
            yield make_event(AgentStreamEventType.TEXT_DELTA, {"text": notice})
            self._context.add_message("assistant", notice)

        yield make_event(AgentStreamEventType.DONE)


from selene_space_agent.react import ReActAgentRuntime  # noqa: E402

AgentRuntime = ReActAgentRuntime

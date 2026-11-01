"""Grid chat control — natural language to action *proposal*.

AI NEVER directly executes any write operation.  The tool returns a
structured proposal the front-end must present for user confirmation;
only after confirmation does the caller invoke the REST / engine API.

Design intent informed by an external reference implementation (upstream
untraceable, provenance undetermined).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from selene_space_types.risk import RiskLevel

from selene_space_agent.tools.base import BaseTool, ToolResult
from selene_space_agent.tools.sentinel_tools import (
    _EX_NAMES,
    _GRID_SYSTEM_PROMPT,
    _build_snapshot,
    _extract_json,
)

_ALLOWED_ACTIONS = {
    "adjust_range",
    "stop_grid",
    "cancel_orders",
    "close_position",
    "reconnect",
    "start_recovery",
    "start_grid",
    "none",
}
_ALLOWED_EXCHANGES = {"de", "ex", "rs"}


@dataclass
class ActionProposal:
    reply: str
    action_type: str = "none"
    exchange: str = ""
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "reply": self.reply,
            "action": {
                "type": self.action_type,
                "exchange": self.exchange,
                "params": self.params,
            },
        }


class GridChatControlTool(BaseTool):
    def __init__(self, engines: dict[str, Any], llm: Any) -> None:
        self._engines = engines
        self._llm = llm

    @property
    def name(self) -> str:
        return "grid_chat_control"

    @property
    def description(self) -> str:
        return (
            "把用户的自然语言指令转成网格操作提案（仅提案，不执行）；"
            "动作与交易所均受白名单约束。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "User instruction in natural language",
                },
            },
            "required": ["message"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.RESTRICTED

    def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            output="",
            error="grid_chat_control is async-only; call async_execute",
        )

    async def async_execute(self, args: dict[str, Any]) -> ToolResult:
        message = args.get("message")
        if not message or not isinstance(message, str):
            return ToolResult(output="", error="Missing required parameter: message")

        snap = _build_snapshot(self._engines)
        prompt = (
            f"{_GRID_SYSTEM_PROMPT}\n"
            "根据用户指令输出 JSON 提案："
            '{"reply":"给用户的话","action":{"type":"none|adjust_range|stop_grid|'
            'cancel_orders|close_position|reconnect|start_recovery|start_grid",'
            '"exchange":"de|ex|rs","params":{}}}\n'
            f"当前快照：{json.dumps(snap, ensure_ascii=False, default=str)}\n"
            f"用户指令：{message}"
        )
        messages = [
            {"role": "system", "content": _GRID_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        try:
            raw = await self._llm.chat(messages)
        except Exception as exc:
            return ToolResult(output="", error=f"LLM 调用失败: {exc}")

        data = _extract_json(raw)
        if data is None:
            # Plain-text reply — pass through without a proposal.
            return ToolResult(output=str(raw))

        reply = str(data.get("reply", ""))
        action = data.get("action") or {}
        if not isinstance(action, dict):
            action = {}
        action_type = str(action.get("type", "none"))
        exchange = str(action.get("exchange", ""))
        params = action.get("params")
        if not isinstance(params, dict):
            params = {}

        # Whitelist enforcement — anything invalid degrades to none.
        valid = action_type == "none" or (
            action_type in _ALLOWED_ACTIONS and exchange in _ALLOWED_EXCHANGES
        )
        if not valid:
            lines = [reply, "（动作不在白名单内，已忽略）"]
            return ToolResult(output="\n".join(lines))

        proposal = ActionProposal(
            reply=reply, action_type=action_type, exchange=exchange, params=params
        )

        lines = [reply]
        if action_type != "none":
            display = _EX_NAMES.get(exchange, exchange)
            lines.append(f"提议操作: {action_type} @ {display} ({exchange})")
            if params:
                lines.append(f"参数: {json.dumps(params, ensure_ascii=False)}")

        return ToolResult(output="\n".join(lines))

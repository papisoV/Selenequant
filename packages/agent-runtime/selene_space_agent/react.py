"""ReAct agent runtime — the primary Observe-Think-Act-Reflect loop."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from typing import Any
from uuid import uuid4

from selene_space_agent.context.manager import ContextManager
from selene_space_agent.events import make_event
from selene_space_agent.prompt_builder import AgentContext, build_system_prompt
from selene_space_agent.tool_catalog import ToolCatalog
from selene_space_llm.schemas import ToolCallResult
from selene_space_types.agent import AgentStreamEvent, AgentStreamEventType

logger = logging.getLogger(__name__)

_LOOP_REPEAT_LIMIT = 3
_NO_PROGRESS_ROUNDS = 3
_LLM_RETRY_LIMIT = 2

# 工具轮用尽时注入的收尾指令(§2.5,对齐 open-source-reference `setup.py:76-89` 的
# wrap_up 节点)。要求模型据已有工具结果写出最终报告,并显式说出没取到的数据 ——
# 宁可交一份带缺口的报告,也不留一句"达到上限"的空停。
_WRAP_UP_INSTRUCTION = (
    "You have used every tool round this report allows. Write your final report "
    "now from the tool results above, and say which data you could not retrieve."
)

# 哨兵:区分"这轮没指定 tools"(→ 默认绑目录工具,常规轮)与"显式要求不绑工具"
# (→ wrap-up 轮传 ``None``)。直接用 ``None`` 当默认值会把两种情况混为一谈。
_TOOLS_DEFAULT: Any = object()

# Called as ``await gate(request_id, tool_name, arguments)``; returns True to
# let the tool run, or anything else (a string reason, None) to deny it. The
# producer (the HTTP turn) supplies this so a human can resolve it out of band.
ApprovalGate = Callable[[str, str, dict[str, Any]], Awaitable[Any]]

# Tools that may run without human confirmation: each was read (not merely
# risk-labelled) and shown to have no side effect — read-only service calls and
# in-memory computation only. Membership is fail-closed by construction: any
# tool NOT listed here needs approval, so a newly added write tool is gated by
# default instead of silently bypassing the gate.
#
# Do NOT add a tool here without a file:line showing its execute() only reads.
# risk_level is advisory and observably wrong (paper_create_account is labelled
# SAFE yet creates an account), so it is never the evidence for membership.
_APPROVAL_EXEMPT: frozenset[str] = frozenset(
    {
        # market data (read services / local klines.db)
        "market_ticker",  # market_tools.py:100
        "market_kline",  # market_tools.py:145
        "market_orderbook",  # market_tools.py:196
        "market_snapshot",  # market_tools.py:285 — klines.db SELECT
        # risk (pure evaluation / rule listing)
        "risk_check",  # risk_tools.py:48 -> RiskEngine.pre_check (no writes)
        "risk_report",  # risk_tools.py:91 — lists rules
        "risk_pressure_check",  # risk_tools.py:164 — get_active_alerts / get_vix_regime
        # explanation / attribution
        "explain_pnl",  # explain_tools.py:60 — reads positions
        "event_kline_attribution",  # event_attribution.py:94 — SELECT only
        # skills (read registry)
        "read_skill",  # read_skill.py:97
        # strategy: read + pure in-memory generate (no persistence)
        "strategy_get_contract",  # strategy_tools.py:210
        "strategy_explain",  # strategy_tools.py:526
        "strategy_generate",  # strategy_tools.py:427 — builds a dict, saves nothing
        "backtest_result",  # strategy_tools.py:367 — reads the results store
        # telegram signals (read)
        "tg_signal_lookup",  # tg_tools.py:115 — SELECT only (tg_tools.py:55)
        "tg_event_impact",  # tg_tools.py:211 — reads positions + SELECT only
        # paper account reads (no order/account mutation)
        "paper_positions",  # paper_tools.py:279 — get_positions only
        "paper_pnl",  # paper_tools.py:324 — get_positions + mark price
    }
)


class ReActAgentRuntime:
    """Stream events for a full reasoning + acting conversation turn.

    Each user message drives up to ``max_tool_rounds`` LLM rounds. A round
    may emit tool calls (executed against the catalog, results fed back) or
    a final text answer. Identical consecutive calls and rounds without any
    progress are cut off with an explanatory notice instead of looping.
    """

    def __init__(
        self,
        llm: Any,
        tool_catalog: ToolCatalog,
        context_manager: ContextManager,
        prompt_context: AgentContext,
        max_tool_rounds: int = 10,
        memory_recall: Callable[[str], Any] | None = None,
        error_replay_recall: Callable[[str], Any] | None = None,
        usage_recorder: Callable[[Any], None] | None = None,
    ) -> None:
        self._llm = llm
        self._catalog = tool_catalog
        self._context = context_manager
        self._prompt_context = prompt_context
        self._max_tool_rounds = max_tool_rounds
        self._memory_recall = memory_recall
        # 异步 recall(hypothesis/mission id)→ prompt 文本,可返回 None。
        # 与 memory_recall(同步、按 query 关键词)并行注入,互不替代。
        self._error_replay_recall = error_replay_recall
        # 每轮真实 token 用量上报给宿主(服务层记账)。由宿主注入而不是在这里
        # import 服务层: 本包不知道账单存在哪。同步回调、不得抛异常。
        self._usage_recorder = usage_recorder

    # ------------------------------------------------------------------
    # Prompt / context helpers
    # ------------------------------------------------------------------

    def _build_messages(self) -> list[dict[str, str]]:
        system_prompt = build_system_prompt(self._prompt_context)
        return [{"role": "system", "content": system_prompt}] + [
            {"role": m.role, "content": m.content} for m in self._context.get_messages()
        ]

    def _tool_specs(self) -> list[dict[str, Any]]:
        try:
            return self._catalog.get_tool_definitions()
        except Exception:
            return []

    def _call_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: Any = _TOOLS_DEFAULT,
        *,
        model: str | None = None,
    ) -> ToolCallResult:
        bound = self._tool_specs() if tools is _TOOLS_DEFAULT else tools
        # ``model`` is forwarded per-call (never mutated onto the shared
        # provider config) so concurrent turns on the same provider cannot
        # bleed a smart-model override into each other.
        kwargs: dict[str, Any] = {"model": model} if model is not None else {}
        return self._llm.chat_with_tools(messages, tools=bound, **kwargs)

    async def _call_with_tools_retrying(
        self, tools: Any = _TOOLS_DEFAULT, *, model: str | None = None
    ) -> tuple[ToolCallResult | None, str | None]:
        """Run chat_with_tools, tolerating timeouts with bounded retries.

        ``tools`` overrides what is bound this round; ``None`` requests a
        tools-free turn (used by the max-rounds wrap-up)."""
        messages = self._build_messages()
        last_error: str | None = None
        for _attempt in range(_LLM_RETRY_LIMIT + 1):
            try:
                result = self._call_with_tools(messages, tools=tools, model=model)
                if hasattr(result, "__await__"):
                    result = await result
                self._report_usage(result)
                return result, None
            except TimeoutError as exc:
                last_error = str(exc) or "LLM timeout"
            except Exception as exc:  # context-length or provider failure
                if type(exc).__name__ == "LLMContextLengthError":
                    self._compact_context()
                    continue
                last_error = f"LLM error: {exc}"
                break
        return None, last_error

    def _report_usage(self, result: ToolCallResult | None) -> None:
        """Hand this round's usage to the host's recorder; never raises.

        Accounting is not worth failing a turn over: a recorder that throws
        would turn a metering bug into a broken conversation.
        """
        if self._usage_recorder is None or result is None:
            return
        try:
            self._usage_recorder(result)
        except Exception:
            logger.warning("agent usage recorder failed", exc_info=True)

    def _compact_context(self) -> None:
        history = self._context.get_messages()
        keep = history[-4:] if len(history) > 4 else history
        self._context.clear()
        summary = "[earlier conversation compacted]"
        self._context.add_message("user", summary)
        for msg in keep:
            self._context.add_message(msg.role, msg.content)

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        approval_gate: ApprovalGate | None = None,
        approval_request_id: str | None = None,
    ) -> dict[str, Any]:
        """Run one tool, gating state-writing tools behind human approval.

        ``approval_gate``/``approval_request_id`` are per-call (never instance
        state): the runtime is LRU-cached per user, so concurrent turns share
        one instance and instance attributes would bleed across sessions.
        """
        tool = self._catalog.get(name)
        if tool is None:
            return {"name": name, "error": f"Tool '{name}' not found"}

        # Fail-closed: only tools proven read-only skip the gate. A tool that
        # needs approval must not run on a forged ``user_confirmed``.
        if approval_gate is not None and name not in _APPROVAL_EXEMPT:
            request_id = approval_request_id or uuid4().hex
            verdict = await approval_gate(request_id, name, arguments)
            if verdict is not True:
                return {
                    "name": name,
                    "error": f"User approval denied: {verdict or 'denied'}",
                }
            # Overwrite (never setdefault): a model-supplied ``user_confirmed``
            # may be present in arguments and must not masquerade as consent.
            arguments = {**arguments, "user_confirmed": True}
        elif approval_gate is None and name not in _APPROVAL_EXEMPT:
            # A caller that supplied no gate runs this write tool unconfirmed.
            # That is allowed (CLI/dev), but never silent: an ungated write is
            # the exact condition the gate exists to prevent, so make it loud.
            logger.warning("ungated_write_tool name=%s reason=no_approval_gate", name)

        try:
            async_execute = getattr(tool, "async_execute", None)
            if async_execute is not None:
                result = await async_execute(arguments)
            else:
                result = tool.execute(arguments)
        except Exception as exc:
            return {"name": name, "error": f"Tool execution error: {exc}"}
        data: dict[str, Any] = {
            "name": name,
            "output": self._context.truncate_tool_result(result.output or ""),
        }
        if result.error:
            data["error"] = result.error
        return data

    @staticmethod
    def _call_signature(call: Any) -> tuple[str, str]:
        args_json = json.dumps(call.arguments, sort_keys=True, default=str)
        return call.name, args_json

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def process_message(
        self,
        message: str,
        *,
        model: str | None = None,
        approval_gate: ApprovalGate | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        """Run one full turn.

        ``model`` optionally overrides the provider model for this turn only,
        forwarded per-call so it never mutates shared provider state.
        ``approval_gate`` is likewise per-call; when omitted (CLI, tests) no
        tool is gated and behaviour is unchanged.
        """
        memory_used_count = 0
        if self._memory_recall is not None:
            try:
                recalled = self._memory_recall(message)
                # 账本召回是异步的(要查库);seed/失败史/决策教训仍是同步。
                # 同一个钩子两种实现,按返回值是否 awaitable 分派。
                if hasattr(recalled, "__await__"):
                    recalled = await recalled
            except Exception:
                logger.debug("memory recall failed", exc_info=True)
                recalled = None
            if recalled:
                # 透明性批2-9: 命中条目数随 DONE 带出,前端渲染"已参考 N 条历史教训"。
                # recall 各路输出都是 "- [...]" 行列表,数非空行即条数。
                memory_used_count = sum(
                    1 for line in recalled.splitlines() if line.strip().startswith("-")
                )
                # Immutable copy: prompt_context is shared across requests.
                self._prompt_context = replace(
                    self._prompt_context, memory_context=recalled
                )
        if self._error_replay_recall is not None:
            try:
                replayed = self._error_replay_recall(message)
                if hasattr(replayed, "__await__"):
                    replayed = await replayed
            except Exception:
                logger.debug("error replay recall failed", exc_info=True)
                replayed = None
            if replayed:
                self._prompt_context = replace(
                    self._prompt_context,
                    memory_context=(
                        self._prompt_context.memory_context + "\n\n" + replayed
                        if self._prompt_context.memory_context
                        else replayed
                    ),
                )
        self._context.add_message("user", message)

        previous_signature: tuple[str, str] | None = None
        repeat_count = 0
        idle_rounds = 0

        for _round in range(self._max_tool_rounds):
            result, failure = await self._call_with_tools_retrying(model=model)

            if failure is not None:
                notice = (
                    f"Aborting after repeated LLM failures ({failure}). "
                    "Please retry your request."
                )
                yield make_event(AgentStreamEventType.TEXT_DELTA, {"text": notice})
                self._context.add_message("assistant", notice)
                break

            assert result is not None
            yield make_event(AgentStreamEventType.THINKING, {})

            tool_calls = getattr(result, "tool_calls", None) or []
            text = (getattr(result, "text", "") or "").strip()

            if not tool_calls and not text:
                idle_rounds += 1
                if idle_rounds >= _NO_PROGRESS_ROUNDS:
                    notice = (
                        f"No progress after {idle_rounds} empty rounds; "
                        "stopping to avoid burning tokens."
                    )
                    yield make_event(AgentStreamEventType.TEXT_DELTA, {"text": notice})
                    self._context.add_message("assistant", notice)
                    break
                continue

            idle_rounds = 0

            if not tool_calls:
                yield make_event(AgentStreamEventType.REFLECTION, {})
                for chunk in _split_stream(text):
                    yield make_event(AgentStreamEventType.TEXT_DELTA, {"text": chunk})
                self._context.add_message("assistant", text)
                if os.getenv("SELENE_DELIVERABLE_OBSERVE", "on").lower() not in (
                    "off",
                    "0",
                    "false",
                ):
                    try:
                        from selene_space_agent.guards.deliverable_classifier import (
                            DeliverableClass,
                            classify_deliverable,
                        )

                        cls = classify_deliverable(text)
                        if cls != DeliverableClass.COMPLETED:
                            logger.warning(
                                "deliverable_class=%s len=%d", cls.value, len(text)
                            )
                    except Exception:
                        logger.debug("deliverable classification failed", exc_info=True)
                break

            signature = self._call_signature(tool_calls[0])
            if signature == previous_signature:
                repeat_count += 1
            else:
                repeat_count = 1
                previous_signature = signature

            if repeat_count >= _LOOP_REPEAT_LIMIT:
                notice = (
                    f"Loop detected: '{signature[0]}' called {_LOOP_REPEAT_LIMIT}x "
                    "with identical arguments. Stopping."
                )
                yield make_event(AgentStreamEventType.TEXT_DELTA, {"text": notice})
                self._context.add_message("assistant", notice)
                break

            yield make_event(AgentStreamEventType.REFLECTION, {})

            observations: list[str] = []
            for call in tool_calls:
                yield make_event(
                    AgentStreamEventType.TOOL_CALL,
                    {"name": call.name, "arguments": call.arguments},
                )
                # Announce the pause before blocking on the gate, so the
                # client can render a confirmation prompt while the tool has
                # not yet run. Judged per call (not once per round) so a round
                # carrying several write calls gates every one of them.
                approval_request_id: str | None = None
                if approval_gate is not None and call.name not in _APPROVAL_EXEMPT:
                    approval_request_id = uuid4().hex
                    yield make_event(
                        AgentStreamEventType.TOOL_APPROVAL_REQUEST,
                        {
                            "tool_call_id": approval_request_id,
                            "tool_name": call.name,
                            "arguments": call.arguments,
                        },
                    )
                outcome = await self._execute_tool(
                    call.name,
                    call.arguments,
                    approval_gate=approval_gate,
                    approval_request_id=approval_request_id,
                )
                # Echo the request id on a gated tool's result so the client
                # can clear exactly the card it raised, instead of guessing
                # by tool name (two calls to the same tool in one round would
                # otherwise clear each other's card).
                if approval_request_id is not None:
                    outcome = {**outcome, "tool_call_id": approval_request_id}
                yield make_event(AgentStreamEventType.TOOL_RESULT, outcome)
                if "error" in outcome:
                    observations.append(f"{call.name} failed: {outcome['error']}")
                else:
                    observations.append(
                        f"{call.name} returned: {outcome.get('output', '')}"
                    )

            self._context.add_message("user", "\n".join(observations))
        else:
            # 工具轮用尽:不再硬停,而是注入收尾指令、解绑工具再问一次,让模型
            # 据已有结果写出最终报告(§2.5)。这一问失败也不吞:退回可见提示。
            # 仍带上本轮的 ``model`` 覆盖:收尾轮产出的是最终报告,不该退回默认模型。
            self._context.add_message("user", _WRAP_UP_INSTRUCTION)
            result, failure = await self._call_with_tools_retrying(
                tools=None, model=model
            )
            wrap_text = (getattr(result, "text", "") or "").strip() if result else ""
            if failure is not None or not wrap_text:
                notice = (
                    f"Reached maximum tool rounds ({self._max_tool_rounds}) "
                    "without a final answer."
                )
                yield make_event(AgentStreamEventType.TEXT_DELTA, {"text": notice})
                self._context.add_message("assistant", notice)
            else:
                yield make_event(AgentStreamEventType.REFLECTION, {})
                for chunk in _split_stream(wrap_text):
                    yield make_event(AgentStreamEventType.TEXT_DELTA, {"text": chunk})
                self._context.add_message("assistant", wrap_text)

        yield make_event(
            AgentStreamEventType.DONE,
            {"memory_used": memory_used_count} if memory_used_count else {},
        )


def _split_stream(text: str, chunk_size: int = 64) -> list[str]:
    """Break an answer into word-boundary chunks for streaming."""
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if len(candidate) > chunk_size and current:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks

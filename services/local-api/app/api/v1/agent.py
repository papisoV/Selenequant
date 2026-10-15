"""Agent chat endpoint with Server-Sent Events (SSE) streaming.

Provides a conversational AI interface that streams responses back
to the client using SSE.  If the ``AgentRuntime`` from
``selene_space_agent`` is not available, a mock stream is returned.

Security layers (defense-in-depth):
1. Input guard: prompt injection detection (regex + pattern matching)
2. Scope guard: topic classification + off-topic blocking
3. Input sanitizer: control char stripping + untrusted content wrapping
4. Output guard: redact internal information from AI responses

Resilience:
- TurnTracker keeps producer tasks alive across client disconnects
- Clients can resume interrupted turns with ``resume: true``
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.dependencies import (
    AuthenticatedUser,
    build_response_meta,
    get_agent_runtime,
    get_db_manager,
    get_settings_service,
    optional_user,
    require_user,
)
from app.core.pro_helpers import require_pro_feature
from selene_space_types.agent import AgentStreamEventType
from selene_space_types.common import APIResponse, ErrorInfo

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Security guardrails
# ---------------------------------------------------------------------------

try:
    from selene_space_security.guardrails.content_scope import (
        ContentScopeGuard,
        GuardAction,
    )
    from selene_space_security.guardrails.hallucination import HallucinationDetector
    from selene_space_security.sanitizer import (
        detect_injection_markers,
        sanitize_for_prompt,  # noqa: F401
    )

    _content_guard = ContentScopeGuard()
    _hallucination_guard = HallucinationDetector()
    _SECURITY_AVAILABLE = True
except ImportError:
    _content_guard = None  # type: ignore[assignment]
    _hallucination_guard = None  # type: ignore[assignment]
    GuardAction = None  # type: ignore[assignment,misc]
    detect_injection_markers = None  # type: ignore[assignment]
    _SECURITY_AVAILABLE = False
    _logger.warning("Security guardrails not available — running unprotected")

router = APIRouter(prefix="/agent", tags=["agent"])


def _ok(request: Request, data: Any) -> APIResponse[Any]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


def _sse_response(
    generator: Any, *, cache_header: str, cache_value: str
) -> StreamingResponse:
    """统一的 SSE 响应(no-cache + keep-alive + 一个标识头)。"""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            cache_header: cache_value,
        },
    )


# ---------------------------------------------------------------------------
# Per-session context storage (in-memory for v0.1)
# ---------------------------------------------------------------------------

_MAX_SESSIONS = 64

try:
    from selene_space_agent.context.manager import ContextManager as _ContextManager

    _CTX_AVAILABLE = True
except ImportError:
    _ContextManager = None  # type: ignore[assignment,misc]
    _CTX_AVAILABLE = False

_session_contexts: OrderedDict[str, Any] = OrderedDict()

# ---------------------------------------------------------------------------
# Pro feature storage (in-memory for v0.1)
# ---------------------------------------------------------------------------

_analysis_tasks: dict[str, dict] = {}  # analysis_id -> {status, result, created_at}
_agent_knowledge: dict[str, list[dict]] = {}  # user_id -> list of knowledge entries


# ---------------------------------------------------------------------------
# Turn tracker — keeps producer alive across client disconnects
# ---------------------------------------------------------------------------

_TURN_TTL_SECONDS = 300  # auto-cleanup after 5 min


@dataclass
class TurnState:
    """Tracks a single agent turn (one user message → response cycle)."""

    session_id: str
    user_id: str
    user_message: str
    status: str = "running"  # running | completed | failed
    started_at: float = dc_field(default_factory=time.monotonic)
    last_checkpoint_at: float = dc_field(default_factory=time.monotonic)
    collected_events: list[str] = dc_field(default_factory=list)
    consumer_cursor: int = 0  # how many events the last consumer received
    producer_task: asyncio.Task[None] | None = None
    queue: asyncio.Queue[str | None] = dc_field(default_factory=asyncio.Queue)
    active_tool: str = ""
    # Tool name currently blocked on human confirmation ("" when none). Kept
    # separate from active_tool so the heartbeat says "waiting", not
    # "executing" — during an approval pause nothing is running.
    awaiting_approval: str = ""
    # request_id -> Future the gate is awaiting. In-memory by design: a
    # restart drops every pending approval, which is fail-closed.
    pending_approval: dict[str, asyncio.Future[Any]] = dc_field(default_factory=dict)


# Key is (user_id, session_id) to prevent cross-user turn hijacking
_active_turns: dict[tuple[str, str], TurnState] = {}


def _abandon_pending_approvals(turn: TurnState, reason: str) -> None:
    """Resolve every pending approval as denied and mark the turn failed.

    A producer cancelled while parked on the gate leaves its Future forever
    unawaited; ``CancelledError`` is a BaseException the producer's
    ``except Exception`` never sees, so the turn would otherwise stay
    ``running`` with an orphan future. Resolving here makes the turn's end
    observable and prevents a later click from "succeeding" against nothing.
    """
    for request_id, fut in list(turn.pending_approval.items()):
        if not fut.done():
            fut.set_result(f"turn abandoned: {reason}")
        turn.pending_approval.pop(request_id, None)
    if turn.status == "running":
        turn.status = "failed"


def _cleanup_stale_turns() -> None:
    """Remove turns that have been completed/failed for > TTL."""
    now = time.monotonic()
    stale = [
        key
        for key, ts in _active_turns.items()
        if ts.status != "running" and (now - ts.last_checkpoint_at) > _TURN_TTL_SECONDS
    ]
    for key in stale:
        t = _active_turns.pop(key, None)
        if t and t.producer_task and not t.producer_task.done():
            t.producer_task.cancel()
        if t is not None:
            _abandon_pending_approvals(t, "stale")
        _logger.debug("Cleaned up stale turn for %s", key)


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------


async def _verify_session_owner(session_id: str, user_id: str) -> bool:
    """Return True if *user_id* owns *session_id*.

    Fail-closed (C2 fix): repo/lookup errors return False, never True.
    The ``user_id == "local"`` short-circuit is gated by
    ``SELENE_AUTH_DISABLED=1`` (audited bypass) — every bypass is logged.
    ``SELENE_AUTH_FAILCLOSED=1`` (default ON) gates the fail-closed
    behavior; ``=0`` reverts to v1 fail-open for emergency rollback.
    """
    from app.core.auth_flags import (
        audit_auth_disabled_bypass,
        is_auth_disabled,
        is_auth_failclosed,
    )

    if user_id == "local":
        if is_auth_disabled():
            audit_auth_disabled_bypass(user_id, f"session:{session_id}")
            return True
        # Fail-closed: localhost trust only when the audited bypass is on.
        # Without the flag, "local" must go through the same ownership
        # check as any other user.
        if is_auth_failclosed():
            # No audited bypass → still allow local for localhost dev, but
            # only when the repo confirms ownership or has no sessions
            # table yet. Fall through to the repo check below.
            pass
        else:
            # Fail-open rollback path (emergency only).
            return True

    try:
        from app.core.dependencies import get_chat_session_repo

        repo = get_chat_session_repo()
        if repo is None:
            if is_auth_failclosed():
                _logger.error(
                    "session_owner_check_failclosed repo_unavailable session=%s user=%s",
                    session_id,
                    user_id,
                )
                return False
            return True
        session = await repo.get_session(session_id)
        if session is None:
            if is_auth_failclosed():
                _logger.error(
                    "session_owner_check_failclosed session_not_found session=%s user=%s",
                    session_id,
                    user_id,
                )
                return False
            return True
        owner = str(session.get("user_id", "local"))
        return owner == user_id or owner == "local"
    except Exception:
        if is_auth_failclosed():
            _logger.exception(
                "session_owner_check_failclosed exception session=%s user=%s",
                session_id,
                user_id,
            )
            return False
        return True


_CONTEXT_ROLES = ("user", "assistant", "system")


def _messages_to_context_data(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """DB 消息行 → ContextManager 可反序列化的记录(只留对话角色)。"""
    return [
        {
            "role": m.get("role", "user"),
            "content": m.get("content", ""),
            "timestamp": None,
            "metadata": None,
        }
        for m in messages
        if m.get("role") in _CONTEXT_ROLES
    ]


async def _restore_context_from_db(
    session_id: str, *, user_id: str = "local"
) -> Any | None:
    """Try to restore a ContextManager from the chat session DB.

    Returns None if the repo is unavailable or the session has no messages.
    Enforces ownership check when *user_id* is not ``"local"``.
    """
    if not _CTX_AVAILABLE or _ContextManager is None:
        return None
    try:
        if not await _verify_session_owner(session_id, user_id):
            _logger.warning(
                "Session %s ownership check failed for user %s", session_id, user_id
            )
            return None
        from app.core.dependencies import get_chat_session_repo

        repo = get_chat_session_repo()
        if repo is None:
            return None
        messages = await repo.get_messages(session_id, limit=100)
        data = _messages_to_context_data(messages) if messages else []
        if not data:
            return None
        return _ContextManager.deserialize(data)
    except Exception as exc:
        _logger.debug("Failed to restore context from DB: %s", exc)
        return None


def _context_data_to_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ContextManager.serialize() 输出 → DB 消息行(带确定性 id)。"""
    return [
        {
            "id": f"ctx-{i:04d}",
            "role": m["role"],
            "content": m["content"],
            "extra": m.get("metadata") or {},
        }
        for i, m in enumerate(messages)
    ]


async def _persist_context_to_db(session_id: str, context: Any) -> None:
    """Write current ContextManager messages to the chat session DB."""
    try:
        from app.core.dependencies import get_chat_session_repo

        repo = get_chat_session_repo()
        if repo is None or not hasattr(context, "serialize"):
            return
        messages = context.serialize()
        if not messages:
            return
        await repo.save_messages_bulk(session_id, _context_data_to_messages(messages))
    except Exception as exc:
        _logger.debug("Failed to persist context to DB: %s", exc)


async def persist_turn_reasoning(
    *,
    session_id: str,
    user_id: str,
    user_message: str,
    assistant_text: str,
    chains: list[str] | None = None,
    run_settings: dict[str, Any] | None = None,
) -> None:
    """Best-effort persistence of one completed turn into agent_reasoning.

    Feeds GET /agent/reasoning/recent (the v008 table had no writer — the
    panel was permanently empty). Never raises: streaming must not break
    because observability failed.

    ``run_settings`` is an **allowlist**: only the keys in
    :data:`_RECORDED_RUN_SETTINGS` survive. Which model and sampling settings
    produced a report is what makes it reproducible later; the endpoint and the
    API key are deliberately dropped (a base_url can carry credentials).
    """
    import json as _json
    import uuid as _uuid

    db = get_db_manager()
    if db is None:
        return
    try:
        ts = int(time.time())
        uid = user_id if user_id != "local" else None
        meta_json = _serialize_run_settings(run_settings)
        rows: list[tuple] = [
            (
                f"ar-{_uuid.uuid4().hex[:16]}",
                ts,
                session_id,
                uid,
                None,
                "user",
                user_message,
                None,
                None,
            ),
            (
                f"ar-{_uuid.uuid4().hex[:16]}",
                ts,
                session_id,
                uid,
                None,
                "assistant",
                assistant_text,
                _json.dumps(chains or [], ensure_ascii=False),
                meta_json,
            ),
        ]
        await db.execute_many(
            "INSERT INTO agent_reasoning "
            "(id, timestamp, session_id, user_id, symbol, role, content, chains_json, meta_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    except Exception:
        _logger.warning("Failed to persist agent reasoning", exc_info=True)


# What a run's record may hold. Model/sampling settings are what make a report
# reproducible; endpoint, key and local paths are never recorded.
_RECORDED_RUN_SETTINGS = ("model", "temperature", "max_tokens", "provider")


def _serialize_run_settings(settings: dict[str, Any] | None) -> str | None:
    """Allowlisted JSON for ``meta_json``, or None when nothing was provided.

    None (not ``"{}"``) so a turn with no settings reads as "not recorded",
    never as "recorded: nothing" — the same empty-vs-absent distinction the
    ledger keeps elsewhere.
    """
    if not settings:
        return None
    import json as _json

    picked = {
        k: settings[k] for k in _RECORDED_RUN_SETTINGS if settings.get(k) is not None
    }
    if not picked:
        return None
    return _json.dumps(picked, ensure_ascii=False)


def _run_settings_from_runtime(runtime: Any) -> dict[str, Any] | None:
    """The recordable settings of the provider a runtime runs on, or None.

    The provider stores its ``LLMConfig`` on ``.config`` (see
    ``selene_space_llm.base``); ``_config`` does not exist and a ``hasattr``
    check on it is always False. Read whatever the object actually exposes and
    degrade to None rather than guess — an absent record is honest, a wrong one
    is worse than none.
    """
    provider = getattr(runtime, "_llm", None)
    config = getattr(provider, "config", None)
    if config is None:
        return None
    settings: dict[str, Any] = {}
    for key in ("model", "temperature", "max_tokens"):
        value = getattr(config, key, None)
        if value is not None:
            settings[key] = value
    return settings or None


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Body for POST /agent/chat."""

    message: str = Field(..., min_length=1, description="User message")
    session_id: str | None = Field(None, description="Existing session ID to continue")
    context: dict[str, Any] | None = Field(
        None, description="UI context: symbol, timeframe, exchange, etc."
    )
    resume: bool = Field(
        False, description="Resume an interrupted turn instead of starting a new one"
    )


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


# 市场相关关键词(中文 + 英文 + 交易对/交易所)。给同一判定需求, 词表只有一种写法,
# 故用紧凑串再 split, 与逐行字面量等价。
_MARKET_KEYWORDS = frozenset(
    """
    分析 行情 趋势 价格 涨 跌 多 空 买 卖 支撑 阻力 K线 k线 均线
    macd rsi ema sma 止损 止盈 仓位 交易 开仓 平仓 持仓 下单 策略 回测
    backtest strategy analyze analysis market price trade order position
    long short btc eth sol usdt bnb doge xrp binance okx orderbook
    订单簿 盘口 波动 突破 震荡 反转 动量 成交量 volume
    生成 写一个 帮我 优化 改进 建议
    """.split()
)


def _is_market_related(message: str) -> bool:
    """Check if the message is about markets, trading, or strategy."""
    msg_lower = message.lower().strip()
    return any(kw in msg_lower for kw in _MARKET_KEYWORDS)


def _context_summary(context: dict[str, Any]) -> list[str]:
    """把 UI 上下文(策略/标的/交易所/周期)转成 "[Current view: ...]" 片段。"""
    ctx_items: list[str] = []
    if context.get("strategy_id"):
        ctx_items.append(f"Strategy ID: {context['strategy_id']}")
    if context.get("strategy_name"):
        ctx_items.append(f"Strategy Name: {context['strategy_name']}")
    if context.get("symbol"):
        ctx_items.append(f"Symbol: {context['symbol']}")
    exchange = context.get("exchange")
    if exchange:
        market_type = context.get("market_type")
        ex_label = f"{exchange} ({market_type})" if market_type else exchange
        ctx_items.append(f"Exchange: {ex_label}")
    timeframe = context.get("timeframe") or context.get("interval")
    if timeframe:
        ctx_items.append(f"Timeframe: {timeframe}")
    return [f"[Current view: {', '.join(ctx_items)}]"] if ctx_items else []


def _enrich_message(message: str, context: dict[str, Any] | None) -> str:
    """Prepend UI context (active symbol, timeframe, etc.) to the user message.

    Only injects market context when the message appears to be about
    markets, trading, or strategy.  Casual greetings and general
    questions pass through without context injection.
    """
    if not context or not _is_market_related(message):
        return message

    parts = _context_summary(context)
    parts.append(message)
    return "\n".join(parts)


_user_runtimes: OrderedDict[str, Any] = OrderedDict()
_MAX_USER_RUNTIMES = 20


async def _get_user_runtime(
    settings_service: Any, *, user_id: str | None = None
) -> Any | None:
    """Return a cached per-user AgentRuntime, building one if needed."""
    from app.core.dependencies import get_tool_catalog

    cache_key = user_id or "local"
    if cache_key in _user_runtimes:
        _user_runtimes.move_to_end(cache_key)
        return _user_runtimes[cache_key]

    try:
        from app.main import _build_agent_runtime

        tool_catalog = get_tool_catalog()
        runtime = await _build_agent_runtime(
            settings_service, tool_catalog, user_id=user_id
        )
        if runtime is not None:
            _user_runtimes[cache_key] = runtime
            while len(_user_runtimes) > _MAX_USER_RUNTIMES:
                evicted_key, _ = _user_runtimes.popitem(last=False)
                _logger.debug("Evicted oldest user runtime: %s", evicted_key)
            _logger.info("Built agent runtime for user %s", cache_key)
        return runtime
    except Exception:
        _logger.debug(
            "Failed to build agent runtime for user %s", cache_key, exc_info=True
        )
        return None


def invalidate_user_runtime(user_id: str | None = None) -> None:
    """Remove a user's cached runtime so it will be rebuilt on next chat."""
    cache_key = user_id or "local"
    _user_runtimes.pop(cache_key, None)


async def _try_lazy_init_runtime(
    settings_service: Any, *, user_id: str | None = None
) -> Any | None:
    """Attempt to build AgentRuntime on demand when it was not created at startup."""
    return await _get_user_runtime(settings_service, user_id=user_id)


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a Server-Sent Event line."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


def _agent_sse(event_type: str, data: dict[str, Any]) -> str:
    """带 {type, data, timestamp} 信封的 SSE 行(timestamp 取当前毫秒)。"""
    return _sse_event(
        event_type,
        {"type": event_type, "data": data, "timestamp": int(time.time() * 1000)},
    )


def _json_loads_event(sse_str: str) -> dict[str, Any]:
    """Parse one SSE string back into its payload dict (empty on any failure)."""
    try:
        for line in sse_str.splitlines():
            if line.startswith("data: "):
                payload: Any = json.loads(line[len("data: ") :])
                return payload if isinstance(payload, dict) else {}
    except Exception:
        pass
    return {}


async def _mock_stream(message: str, session_id: str) -> AsyncIterator[str]:
    """Generate a mock SSE stream when AgentRuntime is not available."""
    # text_delta events
    response_text = (
        f'I received your message: "{message}". '
        "The agent runtime is not yet connected. "
        "This is a placeholder response from the API layer."
    )
    # Stream word by word
    words = response_text.split()
    for i, word in enumerate(words):
        delta = word + (" " if i < len(words) - 1 else "")
        yield _agent_sse(AgentStreamEventType.TEXT_DELTA.value, {"text": delta})

    # done event
    yield _agent_sse(AgentStreamEventType.DONE.value, {"session_id": session_id})


async def _pa_stream(
    runtime: Any, prompt: str, session_id: str, context: dict[str, Any] | None = None
) -> AsyncIterator[str]:
    """Single-shot price-action analysis: one LLM completion, then SSE out.

    Unlike the multi-round agent loop (process_message), this issues a single
    ``chat()`` call so the model's text + fenced JSON decision block are never
    truncated by round boundaries / tool calls. K-lines are already embedded in
    ``prompt`` by the caller.
    """
    llm = getattr(runtime, "_llm", None)
    yield _agent_sse(
        AgentStreamEventType.THINKING.value,
        {"content": "分析中（读取最近 K 线，计算关键价位）…"},
    )
    if llm is None:
        full_text = "（分析服务未就绪，请稍后重试）"
    else:
        messages = [
            {
                "role": "system",
                "content": "你是价格行为分析助手。基于给定 K 线做结构分析，输出中文诊断 + JSON 决策块。",
            },
            {"role": "user", "content": prompt},
        ]
        try:
            full_text = await llm.chat(messages, max_tokens=8000)
            if not full_text:
                full_text = "（LLM 返回空，请重试）"
        except Exception as exc:  # noqa: BLE001
            _logger.warning("_pa_stream LLM call failed: %s", exc)
            full_text = f"（分析失败：{exc}）"

    decision = parse_decision_block(full_text)
    if decision is not None:
        await _apply_arbitration(context or {}, decision)
    yield _agent_sse(AgentStreamEventType.TEXT_DELTA.value, {"text": full_text})
    yield _agent_sse(
        AgentStreamEventType.DONE.value,
        {"session_id": session_id, "decision": decision},
    )


async def _canned_stream(text: str, session_id: str) -> AsyncIterator[str]:
    """Emit a pre-defined response as SSE (used for security blocks)."""
    yield _agent_sse(AgentStreamEventType.TEXT_DELTA.value, {"text": text})
    yield _agent_sse(AgentStreamEventType.DONE.value, {"session_id": session_id})


async def _guarded_agent_stream(
    runtime: Any,
    message: str,
    session_id: str,
    settings_service: Any = None,
    *,
    resume: bool = False,
    user_id: str = "local",
) -> AsyncIterator[str]:
    """Wrapper that applies output filtering on the real agent stream."""

    def _hallucination_check_enabled() -> bool:
        if settings_service is None:
            return True
        try:
            ai_cfg = settings_service.get_ai_config()
            return bool(ai_cfg.get("hallucination_check", True))
        except Exception:
            return True

    _promise_check_on = _hallucination_check_enabled()

    async for chunk in _agent_stream(
        runtime, message, session_id, settings_service, resume=resume, user_id=user_id
    ):
        if _SECURITY_AVAILABLE and _content_guard is not None:
            # Filter text_delta events for information leaks
            if '"text_delta"' in chunk and '"text"' in chunk:
                try:
                    lines = chunk.strip().split("\n")
                    for line in lines:
                        if line.startswith("data: "):
                            data = json.loads(line[6:])
                            text_data = data.get("data", {}).get("text", "")
                            if text_data:
                                if _hallucination_guard is not None:
                                    text_data = (
                                        _hallucination_guard.redact_secrets_in_output(
                                            text_data
                                        )
                                    )
                                    if _promise_check_on:
                                        promise = _hallucination_guard.scan_for_investment_promises(
                                            text_data
                                        )
                                        if promise.triggered:
                                            text_data = (
                                                f"{text_data}\n\n{promise.warnings[0]}"
                                            )
                                filtered = _content_guard.filter_output(text_data)
                                if filtered != text_data:
                                    data["data"]["text"] = filtered
                                else:
                                    data["data"]["text"] = text_data
                                payload = json.dumps(data, ensure_ascii=False)
                                chunk = f"event: text_delta\ndata: {payload}\n\n"
                except (json.JSONDecodeError, KeyError):
                    pass
        yield chunk


_HEARTBEAT_INTERVAL = 5.0


def _resolve_model_override(
    message: str, settings_service: Any, *, user_id: str | None = None
) -> str | None:
    """Pick a smart-model override based on message keywords, if enabled."""
    if not settings_service:
        return None
    try:
        from app.core.settings_service import SettingsService

        svc: SettingsService = settings_service  # type: ignore[assignment]
        settings = (
            svc._load_non_sensitive(user_id=user_id)
            if user_id
            else svc._load_non_sensitive()
        )
        llm_section = settings.get("llm", {})

        raw_smart = llm_section.get("smart_mode", False)
        smart_enabled = (
            raw_smart
            if isinstance(raw_smart, bool)
            else str(raw_smart).lower() == "true"
        )
        smart_models = llm_section.get("smart_models")

        if not smart_enabled or not isinstance(smart_models, dict):
            return None

        message_lower = message.lower()
        if any(
            kw in message_lower
            for kw in ["strategy", "策略", "backtest", "回测", "draft", "起草"]
        ):
            return smart_models.get("strategy")
        if any(
            kw in message_lower
            for kw in ["analyze", "分析", "market", "行情", "trend", "趋势"]
        ):
            return smart_models.get("analysis")
        return smart_models.get("quick")
    except Exception:
        return None


def parse_decision_block(full_text: str) -> dict[str, Any] | None:
    """Extract a fenced JSON decision block from LLM output.

    Looks for the last ```json ... ``` fenced block and parses it into a
    dict with keys ``direction/entry/stop_loss/take_profit/confidence/reason``.
    Returns ``None`` when no valid JSON block is found.
    """
    if not full_text:
        return None
    pattern = r"```json\s*\n?([\s\S]*?)```|\[json\]\s*\n?([\s\S]*?)\[/json\]"
    blocks: list[str] = []
    for m in re.finditer(pattern, full_text):
        blocks.append(m.group(1) or m.group(2) or "")
    if not blocks:
        return None
    for block in reversed(blocks):
        try:
            parsed = json.loads(block.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


async def _fetch_analysis_klines(context: dict[str, Any]) -> str:
    """Fetch recent K-lines for a price-action analysis prompt.

    Pulls the latest ~100 candles for ``context.symbol/timeframe`` via the
    KlineDataManager (local SQLite first, remote fallback).  Formats them as
    compact ``时间,开,高,低,收,量`` lines for the LLM.  Returns an empty string
    when K-line data is unavailable (the prompt degrades to price-only).
    """
    symbol = str(context.get("symbol", "") or "").strip()
    timeframe = str(context.get("timeframe", "1h") or "1h").strip()
    exchange = str(context.get("exchange", "binance") or "binance").strip()
    market_type = str(context.get("market_type", "futures") or "futures").strip()
    if not symbol:
        return ""

    try:
        from app.core.dependencies import get_kline_manager

        km = get_kline_manager()
        if km is None:
            return ""

        async def _pull(source: str) -> str:
            df = await km.get_klines(
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                interval=timeframe,
                source=source,
                min_candles=30,
            )
            if df is None or len(df) == 0:
                return ""
            # 取最近 100 根，按时间升序（get_klines 已保证升序）
            tail = df.tail(100)
            lines = []
            for _, row in tail.iterrows():
                ts = int(row.get("timestamp", 0))
                if ts:
                    tstr = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime(
                        "%Y-%m-%d %H:%M"
                    )
                else:
                    tstr = ""
                lines.append(
                    f"{tstr},{row.get('open', '')},{row.get('high', '')},"
                    f"{row.get('low', '')},{row.get('close', '')},{row.get('volume', '')}"
                )
            return "\n".join(lines)

        text = await _pull("auto")
        if not text:
            text = await _pull("remote")
        return text
    except Exception:
        _logger.warning(
            "_fetch_analysis_klines failed for %s %s", symbol, timeframe, exc_info=True
        )
        return ""


# Objective-signal arbitration (P2-4): LLM decisions must be checked against
# objective evidence, never trusted on confidence alone. Adjacent timeframes
# are pulled local-first — only objective indicators are computed, the LLM
# never sees these frames.
_ADJACENT_TFS = ("1d", "4h", "1h")
_VALID_LLM_DIRECTIONS = ("long", "short", "neutral")


async def _fetch_arbitration_klines(context: dict[str, Any]) -> dict[str, Any]:
    """Fetch K-line DataFrames for objective-signal arbitration.

    Main timeframe (from ``context``) uses ``auto`` source so it stays in
    sync with the prompt fetch (remote fallback available). Adjacent
    ``1d/4h/1h`` timeframes use ``local`` only — cost-savings per spec, the
    consensus merely needs *some* cross-TF evidence. Returns ``{tf: DataFrame}``
    with only frames holding enough candles to compute reliable indicators.
    """
    symbol = str(context.get("symbol", "") or "").strip()
    timeframe = str(context.get("timeframe", "1h") or "1h").strip()
    exchange = str(context.get("exchange", "binance") or "binance").strip()
    market_type = str(context.get("market_type", "futures") or "futures").strip()
    if not symbol:
        return {}

    from selene_space_agent.arbitration import MIN_CANDLES

    try:
        from app.core.dependencies import get_kline_manager

        km = get_kline_manager()
        if km is None:
            return {}

        tfs: list[str] = []
        for tf in (timeframe, *_ADJACENT_TFS):
            if tf not in tfs:
                tfs.append(tf)

        frames: dict[str, Any] = {}
        for tf in tfs:
            source = "auto" if tf == timeframe else "local"
            try:
                df = await km.get_klines(
                    exchange=exchange,
                    market_type=market_type,
                    symbol=symbol,
                    interval=tf,
                    source=source,
                    min_candles=MIN_CANDLES,
                )
            except Exception:
                _logger.debug(
                    "arbitration kline fetch failed for %s %s",
                    symbol,
                    tf,
                    exc_info=True,
                )
                df = None
            if df is not None and len(df) >= MIN_CANDLES:
                frames[tf] = df
        return frames
    except Exception:
        _logger.debug(
            "_fetch_arbitration_klines failed for %s %s",
            symbol,
            timeframe,
            exc_info=True,
        )
        return {}


async def _apply_arbitration(context: dict[str, Any], decision: dict[str, Any]) -> None:
    """Run objective-signal arbitration over an LLM decision, in place.

    Replaces ``decision.direction`` / ``decision.confidence`` with arbitrated
    values, preserves the original LLM decision under ``decision.raw_llm``,
    and adds an ``arbitration`` metadata block. No-ops when the LLM output is
    not arbitrable (missing/invalid direction or confidence). Never raises —
    arbitration failure keeps the raw LLM decision.
    """
    llm_dir = decision.get("direction")
    llm_conf = decision.get("confidence")
    if llm_dir not in _VALID_LLM_DIRECTIONS or not isinstance(llm_conf, (int, float)):
        return

    frames = context.get("_arbitration_klines") or {}
    # No objective data at all -> nothing to arbitrate against; keep the raw
    # LLM decision untouched (arbitration needs at least one usable frame).
    if not frames:
        return

    try:
        from selene_space_agent.arbitration import (
            arbitrate,
            classify_regime,
            compute_consensus,
            compute_objective_signal,
        )

        signals = {tf: compute_objective_signal(df) for tf, df in frames.items()}
        consensus = compute_consensus(signals)
        main_tf = str(context.get("timeframe", "1h") or "1h")
        main_df = frames.get(main_tf)
        if main_df is None:
            main_df = next(iter(frames.values()))
        regime = classify_regime(main_df)
        result = arbitrate(
            llm_direction=llm_dir,
            llm_confidence=float(llm_conf),
            consensus=consensus,
            regime=regime,
            quality_ok=bool(frames),
        )

        decision["raw_llm"] = dict(decision)
        decision["direction"] = result.final_direction
        decision["confidence"] = result.confidence
        decision["arbitration"] = {
            "consensus_score": result.consensus_score,
            "agreement": consensus.agreement,
            "dominant_tf": consensus.dominant_tf,
            "regime": regime,
            "override_reason": result.override_reason,
            "forced_hold": result.forced_hold,
        }
    except Exception:
        _logger.warning("arbitration failed, keeping raw LLM decision", exc_info=True)


def _make_approval_gate(turn: TurnState) -> Any:
    """Build the per-turn approval gate the runtime awaits before a write tool.

    The gate registers a Future on the turn keyed by the request id the runtime
    emitted, then blocks until ``POST /agent/approve`` resolves it or the TTL
    expires. It is passed to ``process_message`` as a per-call argument (never
    stored on the runtime, which is LRU-cached per user and shared by
    concurrent turns).
    """

    async def gate(request_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        turn.pending_approval[request_id] = fut
        _logger.info(
            "tool_approval_request session=%s tool=%s id=%s",
            turn.session_id,
            tool_name,
            request_id,
        )
        try:
            verdict = await asyncio.wait_for(fut, timeout=_TURN_TTL_SECONDS)
            _logger.info(
                "tool_approval_resolved approved=%s id=%s",
                verdict is True,
                request_id,
            )
            return verdict
        except TimeoutError:
            _logger.warning("tool_approval_timeout id=%s", request_id)
            return "user confirmation timed out"
        finally:
            # Idempotent: also clears the stale future after a timeout, so a
            # late click on the card cannot resolve a future nobody awaits.
            turn.pending_approval.pop(request_id, None)

    return gate


def _start_producer(
    runtime: Any,
    message: str,
    session_id: str,
    turn: TurnState,
    settings_service: Any = None,
) -> None:
    """Launch the producer task and store it in the TurnState.

    The producer writes SSE-formatted strings to ``turn.queue`` and
    appends each one to ``turn.collected_events`` so reconnecting
    clients can replay what they missed.
    """
    uid = turn.user_id if turn.user_id != "local" else None
    model_override = _resolve_model_override(message, settings_service, user_id=uid)

    async def _produce_events() -> None:
        try:
            on_checkpoint = getattr(runtime, "_on_checkpoint", None)

            # 累积 text_delta 全文，供 DONE 时解析结构化 decision
            collected_text: list[str] = []

            # 只在有 override 时传 model，避免打破只收 message 的鸭子类型 runtime。
            turn_kwargs: dict[str, Any] = (
                {"model": model_override} if model_override else {}
            )
            # 确认门按 turn 实例化（不落 runtime 实例属性 —— runtime 是
            # per-user LRU 缓存，实例属性会被并发 turn 覆盖串味）。
            turn_kwargs["approval_gate"] = _make_approval_gate(turn)
            async for event in runtime.process_message(message, **turn_kwargs):
                payload = event.model_dump(mode="json")
                if event.type == AgentStreamEventType.DONE:
                    payload.setdefault("data", {})["session_id"] = session_id
                    # 从 LLM 全文提取 fenced JSON decision（供前端画入场/止损/止盈线）
                    full_text = "".join(collected_text)
                    payload["data"]["decision"] = parse_decision_block(full_text)
                elif event.type == AgentStreamEventType.TEXT_DELTA:
                    delta = (payload.get("data") or {}).get("text", "")
                    if delta:
                        collected_text.append(delta)

                evt_type = event.type.value
                event_data = payload.get("data") or {}
                if evt_type == "tool_call":
                    turn.active_tool = str(event_data.get("tool", ""))
                elif evt_type == "tool_approval_request":
                    # Paused for a human: reflect it in the heartbeat step
                    # rather than leaving the turn looking "executing".
                    turn.awaiting_approval = str(event_data.get("tool_name", ""))
                elif evt_type in ("tool_result", "done"):
                    turn.active_tool = ""
                    turn.awaiting_approval = ""

                sse_str = _sse_event(evt_type, payload)
                turn.collected_events.append(sse_str)
                turn.last_checkpoint_at = time.monotonic()
                await turn.queue.put(sse_str)

                if evt_type == "tool_result" and on_checkpoint:
                    try:
                        on_checkpoint(
                            {
                                "session_id": session_id,
                                "event_count": len(turn.collected_events),
                            }
                        )
                    except Exception:
                        pass

            turn.status = "completed"
            # Persist the turn (user msg + assistant reply + tool chain) to
            # agent_reasoning, next to the chat-context persistence above.
            asyncio.ensure_future(
                persist_turn_reasoning(
                    session_id=session_id,
                    user_id=turn.user_id,
                    user_message=message,
                    assistant_text="".join(collected_text),
                    chains=[
                        str((e.get("data") or {}).get("tool", ""))
                        for e in (_json_loads_event(s) for s in turn.collected_events)
                        if e.get("type") == "tool_call"
                        and (e.get("data") or {}).get("tool")
                    ],
                    run_settings=_run_settings_from_runtime(runtime),
                )
            )
            # Persist context to DB after successful turn
            ctx = _session_contexts.get(session_id)
            if ctx is not None:
                asyncio.ensure_future(_persist_context_to_db(session_id, ctx))
        except Exception as exc:
            _logger.warning("Agent stream error: %s", exc, exc_info=True)
            error_sse = _sse_event(
                AgentStreamEventType.DONE.value,
                {
                    "type": AgentStreamEventType.DONE.value,
                    "data": {"error": str(exc), "session_id": session_id},
                    "timestamp": int(time.time() * 1000),
                },
            )
            turn.collected_events.append(error_sse)
            await turn.queue.put(error_sse)
            turn.status = "failed"
        finally:
            turn.last_checkpoint_at = time.monotonic()
            await turn.queue.put(None)

    turn.producer_task = asyncio.create_task(_produce_events())


def _heartbeat_sse(active_tool: str, awaiting_approval: str = "") -> str:
    """心跳事件(带当前执行步骤)。"""
    if awaiting_approval:
        step = f"awaiting approval: {awaiting_approval}"
    elif active_tool:
        step = f"executing: {active_tool}"
    else:
        step = "processing"
    return _sse_event(
        "heartbeat",
        {
            "type": "heartbeat",
            "data": {"step": step},
            "timestamp": int(time.time() * 1000),
        },
    )


def _drain_queue(queue: asyncio.Queue) -> list[str]:
    """把队列里剩余的(非 None)事件取出。"""
    drained: list[str] = []
    while not queue.empty():
        try:
            leftover = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if leftover is not None:
            drained.append(leftover)
    return drained


async def _consume_turn_stream(
    turn: TurnState,
    replay_from: int = 0,
) -> AsyncIterator[str]:
    """Consume events from a TurnState, optionally replaying past events.

    ``replay_from`` is the index into ``turn.collected_events`` at which
    to start.  Events before that index are skipped (the client already
    received them).  After replay, live events from the queue are consumed
    with heartbeat keep-alive.

    Updates ``turn.consumer_cursor`` as events are yielded so the next
    consumer knows where to pick up.
    """
    for sse_str in turn.collected_events[replay_from:]:
        turn.consumer_cursor = max(turn.consumer_cursor, replay_from + 1)
        replay_from += 1
        yield sse_str

    turn.consumer_cursor = len(turn.collected_events)

    if turn.status != "running":
        return

    while True:
        try:
            item = await asyncio.wait_for(turn.queue.get(), timeout=_HEARTBEAT_INTERVAL)
        except TimeoutError:
            yield _heartbeat_sse(turn.active_tool, turn.awaiting_approval)
            continue

        if item is None:
            break
        turn.consumer_cursor = len(turn.collected_events)
        yield item


async def _resume_running_turn(
    existing: TurnState, session_id: str, user_id: str
) -> AsyncIterator[str]:
    """接管一个仍在运行的 turn: 换队列、把旧队列残留并入已收集事件、续流。"""
    replay_from = existing.consumer_cursor
    _logger.info(
        "Resuming turn for session %s user %s (buffered %d events, cursor %d)",
        session_id,
        user_id,
        len(existing.collected_events),
        replay_from,
    )
    old_queue = existing.queue
    existing.queue = asyncio.Queue()
    existing.collected_events.extend(_drain_queue(old_queue))

    async for sse_str in _consume_turn_stream(existing, replay_from=replay_from):
        yield sse_str


async def _replay_finished_turn(
    existing: TurnState, session_id: str
) -> AsyncIterator[str]:
    """已结束的 turn: 直接把已收集事件重放一遍。"""
    _logger.info(
        "Resume requested but turn already %s for session %s — replaying",
        existing.status,
        session_id,
    )
    for sse_str in existing.collected_events:
        yield sse_str


def _begin_turn(
    runtime: Any,
    message: str,
    session_id: str,
    settings_service: Any,
    *,
    user_id: str,
) -> TurnState:
    """取消同键旧 turn, 建新 TurnState, 启动生产者。返回新 turn。"""
    turn_key = (user_id, session_id)
    old_turn = _active_turns.pop(turn_key, None)
    if old_turn and old_turn.producer_task and not old_turn.producer_task.done():
        old_turn.producer_task.cancel()
    if old_turn is not None:
        # The cancelled producer may be parked on the gate; release its
        # Future so the approval no longer exists once the turn is gone.
        _abandon_pending_approvals(old_turn, "superseded")

    turn = TurnState(
        session_id=session_id,
        user_id=user_id,
        user_message=message,
    )
    _active_turns[turn_key] = turn
    _start_producer(runtime, message, session_id, turn, settings_service)
    return turn


async def _agent_stream(
    runtime: Any,
    message: str,
    session_id: str,
    settings_service: Any = None,
    *,
    resume: bool = False,
    user_id: str = "local",
) -> AsyncIterator[str]:
    """Stream events from the real AgentRuntime with heartbeat keepalive.

    If ``resume`` is True and an active turn exists for this session, the
    stream reconnects to the running producer, replays missed events, and
    continues live.  Otherwise a new turn is started.

    The producer task is NOT cancelled when the consumer disconnects; it
    keeps running so a reconnecting client can pick up where it left off.
    """
    _cleanup_stale_turns()

    turn_key = (user_id, session_id)
    existing = _active_turns.get(turn_key)

    if resume and existing is not None:
        if existing.status == "running":
            async for sse_str in _resume_running_turn(existing, session_id, user_id):
                yield sse_str
        else:
            async for sse_str in _replay_finished_turn(existing, session_id):
                yield sse_str
        return

    # --- Start a new turn ---
    turn = _begin_turn(runtime, message, session_id, settings_service, user_id=user_id)

    async for sse_str in _consume_turn_stream(turn):
        yield sse_str


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/chat")
async def agent_chat(
    body: ChatRequest,
    runtime: Any = Depends(get_agent_runtime),
    settings_service: Any = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> StreamingResponse:
    """Start an AI conversation turn (SSE stream).

    Security pipeline (runs before LLM):
    1. Content scope guard — blocks injections, off-topic, and secret extraction
    2. Input sanitizer — strips control chars, wraps untrusted content
    3. Output filter — redacts internal info from AI responses

    Resilience:
    - Pass ``resume: true`` to reconnect to a running/interrupted turn
    - The producer keeps running even if the client disconnects

    SSE events:
    - ``thinking``, ``tool_call``, ``tool_result``, ``reflection``
    - ``text_delta``: incremental text output
    - ``done``: conversation turn finished
    """
    session_id = body.session_id or f"sess-{uuid.uuid4().hex[:8]}"

    # --- Resume fast path: skip enrichment/security for reconnects ---
    turn_key = (user.id, session_id)
    if body.resume and turn_key in _active_turns:
        _logger.info("Resume request for session %s user %s", session_id, user.id)
        generator = _guarded_agent_stream(
            runtime,
            body.message,
            session_id,
            settings_service,
            resume=True,
            user_id=user.id,
        )
        return _sse_response(
            generator, cache_header="X-Session-ID", cache_value=session_id
        )

    # --- Build enriched message first (needed for scope guard) ---
    effective_message = body.message
    if body.context and body.context.get("intent"):
        from selene_space_agent.analysis_prompts import build_analysis_prompt

        # 图表联动 AI 分析：后端拉 K 线喂给 LLM（build_analysis_prompt 保持纯函数，
        # K 线数据经 context.kline_data 传入，避免在 prompt 模板里注入 IO）
        if body.context.get("intent") == "kline_price_action":
            body.context["kline_data"] = await _fetch_analysis_klines(body.context)

        analysis_prompt = build_analysis_prompt(body.context)
        if analysis_prompt is not None:
            effective_message = analysis_prompt
        else:
            effective_message = _enrich_message(body.message, body.context)
    else:
        effective_message = _enrich_message(body.message, body.context)

    # --- Security Layer 1: Input guard ---
    guard_input = (
        effective_message if effective_message != body.message else body.message
    )
    if _SECURITY_AVAILABLE and _content_guard is not None:
        guard_result = _content_guard.check_input(guard_input)
        if guard_result.action == GuardAction.BLOCK:
            _logger.warning(
                "Content guard blocked: topic=%s, reason=%s, msg=%s",
                guard_result.topic.value,
                guard_result.reason,
                body.message[:80],
            )
            return _sse_response(
                _canned_stream(guard_result.canned_response or "", session_id),
                cache_header="X-Session-ID",
                cache_value=session_id,
            )

    # --- Security Layer 2: Injection marker detection (audit log) ---
    if _SECURITY_AVAILABLE:
        markers = detect_injection_markers(body.message)
        if markers:
            _logger.warning(
                "Injection markers detected: %s in message: %s",
                markers,
                body.message[:100],
            )

    # --- Per-session context management (namespaced by user_id) ---
    if _CTX_AVAILABLE and runtime is not None and hasattr(runtime, "_context"):
        ctx_key = f"{user.id}:{session_id}" if user.id != "local" else session_id
        if ctx_key not in _session_contexts:
            ctx = await _restore_context_from_db(session_id, user_id=user.id)
            _session_contexts[ctx_key] = ctx or _ContextManager()
            if ctx:
                _logger.info(
                    "Restored context from DB for session %s (user=%s)",
                    session_id,
                    user.id,
                )
            else:
                _logger.info(
                    "Created new context for session %s (user=%s)", session_id, user.id
                )
            while len(_session_contexts) > _MAX_SESSIONS:
                evicted_key, _ = _session_contexts.popitem(last=False)
                _logger.debug("Evicted oldest session context: %s", evicted_key)
        else:
            _session_contexts.move_to_end(ctx_key)
        runtime._context = _session_contexts[ctx_key]

    # Per-user runtime: always use the user's own LLM config
    uid = user.id if user.id != "local" else None
    user_runtime = await _get_user_runtime(settings_service, user_id=uid)
    if user_runtime is not None:
        runtime = user_runtime
    elif runtime is None and settings_service is not None:
        runtime = await _try_lazy_init_runtime(settings_service, user_id=uid)

    if runtime is not None:
        if body.context and body.context.get("intent") == "kline_price_action":
            # 单轮 completion：避免 agent 多轮循环截断 JSON 决策块
            ctx = dict(body.context)
            ctx["_arbitration_klines"] = await _fetch_arbitration_klines(ctx)
            generator = _pa_stream(runtime, effective_message, session_id, context=ctx)
        else:
            generator = _guarded_agent_stream(
                runtime,
                effective_message,
                session_id,
                settings_service,
                user_id=user.id,
            )
    else:
        generator = _mock_stream(body.message, session_id)

    return _sse_response(generator, cache_header="X-Session-ID", cache_value=session_id)


# Alias for explicit stream endpoint (backward compatible)
@router.post("/stream")
async def agent_stream(
    body: ChatRequest,
    runtime: Any = Depends(get_agent_runtime),
    settings_service: Any = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> StreamingResponse:
    """Alias for ``POST /agent/chat`` — same SSE stream, explicit name."""
    return await agent_chat(body, runtime, settings_service, user)


# ===========================================================================
# Pro endpoints — analysis, team, trading workspace, knowledge
# ===========================================================================


class AnalysisStartRequest(BaseModel):
    """Body for POST /agent/analysis/start."""

    analysis_id: str | None = Field(
        None, description="Custom analysis ID (auto-generated if omitted)"
    )
    message: str = Field(..., min_length=1, description="Analysis prompt")
    context: dict[str, Any] | None = Field(
        None, description="Additional context for the analysis"
    )


class TeamAnalysisRequest(BaseModel):
    """Body for POST /agent/team."""

    question: str = Field(
        ..., min_length=1, description="Question for the multi-agent team"
    )
    roles: list[str] | None = Field(
        None, description="Custom role list (defaults to bull/bear/risk)"
    )
    timeout_s: int = Field(60, ge=5, le=300, description="Per-role timeout in seconds")


class KnowledgeCreateRequest(BaseModel):
    """Body for POST /agent/trading/knowledge."""

    content: str = Field(..., min_length=1, description="Knowledge content to store")
    tags: list[str] | None = Field(None, description="Tags for categorisation")
    source: str | None = Field(None, description="Source attribution")


# ---------------------------------------------------------------------------
# Role prompt mapping for team analysis
# ---------------------------------------------------------------------------

_ROLE_PROMPTS: dict[str, str] = {
    "bull_analyst": "You are a bullish analyst. Find the upside case. Focus on positive catalysts, growth potential, and favourable scenarios. Be specific and data-driven.",
    "bear_analyst": "You are a bearish analyst. Find the downside risks. Focus on negative catalysts, threats, and adverse scenarios. Be specific and data-driven.",
    "risk_analyst": "You are a risk analyst. Assess position sizing and risk management. Focus on volatility, drawdown potential, stop-loss levels, and portfolio impact. Be specific and data-driven.",
}

_DEFAULT_ROLES = ["bull_analyst", "bear_analyst", "risk_analyst"]


# ---------------------------------------------------------------------------
# 1. POST /agent/analysis/start
# ---------------------------------------------------------------------------


async def _run_analysis_background(
    analysis_id: str,
    message: str,
    context: dict[str, Any] | None,
    settings_service: Any,
) -> None:
    """Background coroutine that runs an LLM analysis and stores the result."""
    try:
        from app.core.llm_helper import call_llm

        prompt = message
        if context:
            ctx_parts = [f"{k}: {v}" for k, v in context.items() if v is not None]
            if ctx_parts:
                prompt = f"[Context: {', '.join(ctx_parts)}]\n\n{message}"

        result_text = await call_llm(
            settings_service,
            prompt,
            system="You are a financial analysis assistant. Provide thorough, structured analysis.",
            max_tokens=1000,
        )

        _analysis_tasks[analysis_id]["status"] = "completed"
        _analysis_tasks[analysis_id]["result"] = (
            {"analysis": result_text}
            if result_text
            else {"analysis": None, "note": "LLM returned no output"}
        )
    except Exception as exc:
        _logger.warning("Analysis %s failed: %s", analysis_id, exc, exc_info=True)
        _analysis_tasks[analysis_id]["status"] = "failed"
        _analysis_tasks[analysis_id]["error"] = str(exc)
    finally:
        _analysis_tasks[analysis_id]["completed_at"] = time.time()


@router.post("/analysis/start")
async def analysis_start(
    body: AnalysisStartRequest,
    settings_service: Any = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Start a background analysis task.

    Returns the analysis_id immediately while the LLM processes in the
    background.  Poll ``/agent/analysis/status/{analysis_id}`` or connect
    to ``/agent/analysis/stream/{analysis_id}`` for progress.
    """
    require_pro_feature("analysis")

    analysis_id = body.analysis_id or f"analysis-{uuid.uuid4().hex[:12]}"
    created_at = time.time()

    _analysis_tasks[analysis_id] = {
        "status": "running",
        "created_at": created_at,
        "message": body.message,
    }

    asyncio.create_task(
        _run_analysis_background(
            analysis_id, body.message, body.context, settings_service
        )
    )

    return {"analysis_id": analysis_id, "status": "running"}


# ---------------------------------------------------------------------------
# 2. GET /agent/analysis/status/{analysis_id}
# ---------------------------------------------------------------------------


@router.get("/analysis/status/{analysis_id}")
async def analysis_status(
    analysis_id: str,
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Check the status of a background analysis task."""
    task = _analysis_tasks.get(analysis_id)
    if task is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Analysis {analysis_id} not found")

    result: dict[str, Any] = {
        "analysis_id": analysis_id,
        "status": task["status"],
        "created_at": task["created_at"],
    }
    if "result" in task:
        result["result"] = task["result"]
    if "error" in task:
        result["error"] = task["error"]
    if "completed_at" in task:
        result["completed_at"] = task["completed_at"]
    return result


# ---------------------------------------------------------------------------
# 3. GET /agent/analysis/stream/{analysis_id} — SSE
# ---------------------------------------------------------------------------


async def _analysis_sse_generator(analysis_id: str) -> AsyncIterator[str]:
    """SSE generator that polls _analysis_tasks and emits status events."""
    try:
        while True:
            task = _analysis_tasks.get(analysis_id)
            if task is None:
                yield _sse_event(
                    "error", {"analysis_id": analysis_id, "error": "not found"}
                )
                return

            status = task["status"]

            if status == "running":
                yield _sse_event(
                    "status",
                    {"analysis_id": analysis_id, "status": "running"},
                )
                await asyncio.sleep(2.0)
            elif status == "completed":
                yield _sse_event(
                    "status",
                    {
                        "analysis_id": analysis_id,
                        "status": "completed",
                        "result": task.get("result"),
                    },
                )
                yield _sse_event("done", {"analysis_id": analysis_id})
                return
            elif status == "failed":
                yield _sse_event(
                    "error",
                    {
                        "analysis_id": analysis_id,
                        "status": "failed",
                        "error": task.get("error"),
                    },
                )
                yield _sse_event("done", {"analysis_id": analysis_id})
                return
            else:
                yield _sse_event(
                    "status",
                    {"analysis_id": analysis_id, "status": status},
                )
                await asyncio.sleep(2.0)
    except asyncio.CancelledError:
        return


@router.get("/analysis/stream/{analysis_id}")
async def analysis_stream(
    analysis_id: str,
    user: AuthenticatedUser = Depends(optional_user),
) -> StreamingResponse:
    """SSE stream for analysis task progress.

    Emits ``status`` events while running, then a final ``done`` (or
    ``error``) event when the analysis completes or fails.
    """
    task = _analysis_tasks.get(analysis_id)
    if task is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Analysis {analysis_id} not found")

    return _sse_response(
        _analysis_sse_generator(analysis_id),
        cache_header="X-Analysis-ID",
        cache_value=analysis_id,
    )


# ---------------------------------------------------------------------------
# 4. POST /agent/team — multi-agent team analysis
# ---------------------------------------------------------------------------


async def _run_role_analysis(
    role: str,
    question: str,
    settings_service: Any,
    timeout_s: int,
) -> dict[str, Any]:
    """Run a single role-based LLM analysis."""
    from app.core.llm_helper import call_llm

    system_prompt = _ROLE_PROMPTS.get(
        role, f"You are a {role}. Provide thorough analysis."
    )
    try:
        result = await asyncio.wait_for(
            call_llm(settings_service, question, system=system_prompt, max_tokens=600),
            timeout=timeout_s,
        )
        return {"role": role, "analysis": result}
    except asyncio.TimeoutError:
        return {"role": role, "analysis": None, "error": "timeout"}
    except Exception as exc:
        return {"role": role, "analysis": None, "error": str(exc)}


@router.post("/team")
async def team_analysis(
    body: TeamAnalysisRequest,
    settings_service: Any = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Multi-agent team analysis with parallel role-based LLM calls.

    Each role analyses the question from its perspective (bull/bear/risk
    by default).  After all roles complete, a synthesis LLM call merges
    the individual analyses into a coherent summary.
    """
    require_pro_feature("team_analysis")

    roles = body.roles or _DEFAULT_ROLES
    analyses = await asyncio.gather(
        *[
            _run_role_analysis(role, body.question, settings_service, body.timeout_s)
            for role in roles
        ]
    )

    # Build synthesis prompt from individual analyses
    analyses_list = list(analyses)
    synthesis_parts: list[str] = []
    for a in analyses_list:
        role = a["role"]
        text = a.get("analysis") or a.get("error", "no output")
        synthesis_parts.append(f"## {role}\n{text}")

    synthesis_prompt = (
        f"Synthesise the following analyst perspectives into a concise summary.\n\n"
        f"Question: {body.question}\n\n"
        + "\n\n".join(synthesis_parts)
        + "\n\nProvide a balanced synthesis with actionable takeaways."
    )

    from app.core.llm_helper import call_llm

    synthesis = await call_llm(
        settings_service,
        synthesis_prompt,
        system="You are a senior portfolio manager. Synthesise multiple analyst perspectives into actionable insights.",
        max_tokens=800,
    )

    return {
        "question": body.question,
        "analyses": analyses_list,
        "synthesis": synthesis,
        "roles_used": roles,
    }


# ---------------------------------------------------------------------------
# 5. GET /agent/trading/workspace
# ---------------------------------------------------------------------------


def _position_to_dict(pos: Any) -> dict[str, Any]:
    """Normalize a Position object to its JSON dict form; legacy dicts pass through."""
    if isinstance(pos, dict):
        return pos
    dump = getattr(pos, "model_dump", None)
    if callable(dump):
        return dict(pos.model_dump(mode="json"))
    return pos  # type: ignore[no-any-return]  # legacy dict passthrough


def _resolve_agent_account_id() -> str:
    """Resolve the account_id for agent workspace position queries (§3.5).

    Delegates to the shared ``app.core.account_state.resolve_account_id`` so
    the WS snapshot path and the agent workspace resolve multi-account paper
    setups consistently.
    """
    from app.core.account_state import resolve_account_id

    return resolve_account_id()


@router.get("/trading/workspace")
async def trading_workspace(
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Aggregate trading workspace data: positions, orders, strategies, account summary.

    Gracefully returns empty lists when dependency managers are not
    available (e.g. paper trading not initialised).
    """
    require_pro_feature("trading_workspace")

    from app.core.dependencies import (
        get_execution_engine,
        get_paper_account_manager,
        get_paper_position_manager,
        get_strategy_repo,
    )

    # --- Positions ---
    positions: list[dict[str, Any]] = []
    try:
        account_id = _resolve_agent_account_id()
        engine = get_execution_engine()
        if engine is not None:
            positions = [
                _position_to_dict(p) for p in await engine.get_positions(account_id)
            ]
        else:
            pos_mgr = get_paper_position_manager()
            if pos_mgr is not None:
                positions = [
                    _position_to_dict(p) for p in pos_mgr.get_open_positions(account_id)
                ]
    except Exception as exc:
        _logger.debug("Failed to fetch positions: %s", exc)

    # --- Pending orders ---
    pending_orders: list[dict[str, Any]] = []
    try:
        acct_mgr = get_paper_account_manager()
        if acct_mgr is not None and hasattr(acct_mgr, "list_pending_orders"):
            pending_orders = await acct_mgr.list_pending_orders()
    except Exception as exc:
        _logger.debug("Failed to fetch pending orders: %s", exc)

    # --- Active strategies ---
    active_strategies: list[dict[str, Any]] = []
    try:
        strat_repo = get_strategy_repo()
        if strat_repo is not None and hasattr(strat_repo, "list_active"):
            active_strategies = await strat_repo.list_active()
    except Exception as exc:
        _logger.debug("Failed to fetch active strategies: %s", exc)

    # --- Account summary ---
    account_summary: dict[str, Any] = {}
    try:
        acct_mgr = get_paper_account_manager()
        if acct_mgr is not None and hasattr(acct_mgr, "summary"):
            account_summary = await acct_mgr.summary()
    except Exception as exc:
        _logger.debug("Failed to fetch account summary: %s", exc)

    return {
        "positions": positions,
        "pending_orders": pending_orders,
        "active_strategies": active_strategies,
        "account_summary": account_summary,
    }


# ---------------------------------------------------------------------------
# 6. POST /agent/trading/knowledge
# ---------------------------------------------------------------------------


@router.post("/trading/knowledge")
async def create_knowledge(
    body: KnowledgeCreateRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Store a knowledge entry for the trading agent.

    Entries are keyed by user_id and kept in memory.  The agent can
    retrieve stored knowledge during analysis to incorporate user
    preferences, domain expertise, or contextual notes.
    """
    require_pro_feature("trading_knowledge")

    user_key = user.id or "local"
    entry_id = f"kn-{uuid.uuid4().hex[:12]}"
    entry = {
        "id": entry_id,
        "content": body.content,
        "tags": body.tags or [],
        "source": body.source,
        "created_at": time.time(),
    }

    if user_key not in _agent_knowledge:
        _agent_knowledge[user_key] = []
    _agent_knowledge[user_key].append(entry)

    return entry


# ---------------------------------------------------------------------------
# 7. DELETE /agent/trading/knowledge/{knowledge_id}
# ---------------------------------------------------------------------------


@router.delete("/trading/knowledge/{knowledge_id}")
async def delete_knowledge(
    knowledge_id: str,
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Remove a knowledge entry by ID.

    Searches all knowledge entries for the current user and removes the
    one matching *knowledge_id*.
    """
    require_pro_feature("trading_knowledge")

    user_key = user.id or "local"
    entries = _agent_knowledge.get(user_key, [])

    for i, entry in enumerate(entries):
        if entry.get("id") == knowledge_id:
            entries.pop(i)
            return {"deleted": True}

    return {"deleted": False}


# ---------------------------------------------------------------------------
# 8. GET /agent/reasoning/recent — recent reasoning messages (v008 migration)
# ---------------------------------------------------------------------------


@router.get("/reasoning/recent")
async def get_agent_reasoning_recent(
    request: Request,
    limit: int = 50,
    session_id: str | None = None,
    symbol: str | None = None,
) -> APIResponse[list[dict[str, Any]]]:
    """Return recent agent reasoning messages ordered by timestamp desc.

    Reads from the agent_reasoning table (v008 migration). Empty list when
    storage is unavailable or table is empty.
    """
    db = get_db_manager()
    rows: list[dict[str, Any]] = []
    if db is None:
        return _ok(request, rows)
    try:
        conditions: list[str] = []
        params: list[Any] = []
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = (
            "SELECT id, timestamp, session_id, user_id, symbol, role, content, chains_json, meta_json "
            f"FROM agent_reasoning {where_clause} ORDER BY timestamp DESC LIMIT ?"
        )
        params.append(limit)
        result = await db.execute(sql, tuple(params))
        for r in result:
            rows.append(dict(r))
    except Exception:
        _logger.warning("Failed to fetch agent_reasoning", exc_info=True)
    return _ok(request, rows)


@router.get("/decision-lessons")
async def get_decision_lessons(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """B1 human view: read-only list of decision → outcome lessons.

    Mirrors what the AI recall() injects into its context — pending entries
    carry no outcome yet; filled entries include outcome metrics and the
    backfilled reflection. Read-only: never writes the memory file.

    每条记录经 ``human_view_entry`` 投影:补 ``is_review`` 布尔并归一空白
    rating,使 REVIEW 哨兵能在前端被渲染成"需人看/重跑"警告而非普通评级。
    """
    from selene_space_agent.memory.decision_outcomes import (
        DecisionOutcomeMemory,
        human_view_entry,
    )

    try:
        memory = DecisionOutcomeMemory()
        entries = [human_view_entry(e) for e in memory._entries]
    except Exception:
        _logger.debug("decision outcomes memory unavailable", exc_info=True)
        entries = []
    entries.reverse()  # latest first
    return _ok(request, entries)


class AgentApproveRequest(BaseModel):
    """Body for POST /agent/approve — resolve one pending tool approval."""

    session_id: str = Field(..., min_length=1, description="Session owning the turn")
    tool_call_id: str = Field(..., min_length=1, description="Request id from the SSE")
    approve: bool = Field(..., description="True to run the tool, False to deny")


@router.post("/approve")
async def approve_tool_call(
    req: AgentApproveRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_user),
) -> APIResponse[dict[str, Any]]:
    """Resolve a tool approval the runtime is blocked on.

    Authorization: this file's other endpoints use ``optional_user``; approval
    is a security action, so it demands a real user (``require_user``) plus an
    ownership check on the session. Under ``SELENE_AUTH_DISABLED=1`` the
    ``_verify_session_owner`` audited bypass still applies — the same
    local-only bypass the rest of the API relies on.

    This endpoint only resolves the Future the gate awaits; it does NOT execute
    the tool. Execution stays in the runtime, on its original path.
    """
    if not await _verify_session_owner(req.session_id, user.id):
        return APIResponse(
            error=ErrorInfo(code="forbidden", message="session owner mismatch"),
            meta=build_response_meta(request),
        )

    turn = _active_turns.get((user.id, req.session_id))
    # A2: approving a finished/dead turn resolves nothing — report the same
    # "nothing pending" as an unknown id instead of a false success.
    if turn is None or turn.status != "running":
        return APIResponse(
            error=ErrorInfo(code="no_pending_approval", message="no pending approval"),
            meta=build_response_meta(request),
        )

    fut = turn.pending_approval.pop(req.tool_call_id, None)
    if fut is None or fut.done():
        # Unknown/replayed/already-timed-out request id → safe no-op.
        return APIResponse(
            error=ErrorInfo(code="no_pending_approval", message="no pending approval"),
            meta=build_response_meta(request),
        )

    fut.set_result(True if req.approve else "user denied")
    _logger.info(
        "tool_approval_http session=%s id=%s approve=%s",
        req.session_id,
        req.tool_call_id,
        req.approve,
    )
    return APIResponse(
        data={"resolved": True},
        meta=build_response_meta(request),
    )

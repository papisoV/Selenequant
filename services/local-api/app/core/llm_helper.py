"""LLM helper for intelligence endpoints.

Provides a lightweight wrapper to call the configured LLM via
SettingsService + selene_space_llm. Falls back gracefully when no LLM
is configured (returns None, caller uses rule-based logic).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_llm_available: bool | None = None


async def is_llm_configured(settings_service: Any) -> bool:
    """Check if LLM is configured without making a call."""
    global _llm_available
    try:
        config = await settings_service.resolve_llm_full_config()
        api_key = config.get("api_key")
        if api_key:
            _llm_available = True
            return True
    except Exception:
        pass
    _llm_available = False
    return False


async def build_provider(settings_service: Any) -> Any | None:
    """Build a provider from the keyring-backed settings chain.

    Same chain as ``decision_workflow_service._get_trading_adapter``
    (resolve_llm_full_config -> LLMConfig -> OpenAICompatProvider), but
    returns ``None`` instead of raising when no key is configured — callers
    here treat "not configured" as a normal, non-fatal state.

    Returns ``None`` on any failure; the caller degrades gracefully.
    """
    try:
        from selene_space_llm.base import LLMConfig
        from selene_space_llm.openai_compat import OpenAICompatProvider

        cfg = await settings_service.resolve_llm_full_config()
        api_key = cfg.get("api_key")
        if not api_key:
            return None

        # OpenAICompatProvider appends /chat/completions itself, so base_url
        # must end at the version segment; tolerate a missing /v1 (same
        # normalization as decision_workflow_service._get_trading_adapter).
        base_url = str(cfg.get("base_url") or "").rstrip("/")
        if base_url and not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        return OpenAICompatProvider(
            LLMConfig(
                model=cfg.get("model") or "default",
                api_key=api_key,
                base_url=base_url or None,
                temperature=0.0,
                # Grid sentinel tools call chat() bare with no retry
                # (sentinel_tools.py:201); glm-style reasoning models spend
                # thinking tokens against max_tokens and return empty content
                # at the 4096 default (see call_llm above). 16384 is the
                # validated budget from the decision-workflow template.
                max_tokens=16384,
            )
        )
    except Exception as exc:
        logger.warning("LLM provider build from keyring failed: %s", exc)
        return None


async def call_llm(
    settings_service: Any,
    prompt: str,
    system: str = "You are a financial analysis assistant. Be concise and factual.",
    max_tokens: int = 500,
    timeout: float = 30.0,
    source: str = "helper",
) -> str | None:
    """Call the configured LLM with a prompt. Returns None if not configured or on error.

    Every call's real token usage is recorded in the daily ledger, and a
    configured daily ceiling pauses *new* unattended calls once it is reached
    (returning None, which every caller already treats as "no LLM"). The
    default ceiling is 0 = no cap, so nothing changes unless the user sets one.
    """
    try:
        from selene_space_llm.base import LLMConfig, LLMMessage, LLMRole
        from selene_space_llm.openai_compat import OpenAICompatProvider

        from app.core.llm_usage import budget_exceeded, get_ledger

        if budget_exceeded(settings_service):
            logger.warning(
                "Daily LLM token ceiling reached — skipping %s call (raise or clear it in Settings)",
                source,
            )
            return None

        config = await settings_service.resolve_llm_full_config()
        api_key = config.get("api_key")
        if not api_key:
            return None

        llm_config = LLMConfig(
            model=config.get("model") or "default",
            api_key=api_key,
            base_url=config.get("base_url") or None,
        )
        provider = OpenAICompatProvider(llm_config)

        # provider._build_payload 取 m.role / m.content,必须传 LLMMessage
        # 而不是裸 dict(dict 会在 "'dict' object has no attribute 'role'" 处炸)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        llm_messages = [
            LLMMessage(role=LLMRole(m["role"]), content=m["content"]) for m in messages
        ]

        async def _do_call(
            effective_max_tokens: int | None,
        ) -> tuple[str | None, bool]:
            # chat_with_usage, not chat: chat discards the provider's usage
            # block, and an unmetered call is invisible to the daily ceiling.
            text, usage = await provider.chat_with_usage(
                messages=llm_messages, max_tokens=effective_max_tokens
            )
            get_ledger().record(
                source=source,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                model=llm_config.model,
            )
            # finish_reason="length" 表示回复被 max_tokens 砍断。推理模型的
            # thinking 与正文共用这块预算:实测同一 prompt 在 700 预算下有
            # 一半的调用被截在 JSON 中间,而 HTTP 仍是 200、内容也非空。
            # 以前这里只看"空不空",半截 JSON 被当成完整回复交给调用方,
            # 解析失败后记成"模型不听话"(生产库里 5 条 unparseable_response
            # 全是这个形状)。截断必须与"没回"走同一条重试。
            truncated = getattr(usage, "finish_reason", "") == "length"
            response = text
            if response and isinstance(response, str):
                return response.strip(), truncated
            if response and hasattr(response, "content"):
                content: str = response.content
                return content.strip(), truncated
            logger.warning(
                "LLM returned empty/unexpected response (type=%s, model=%s, max_tokens=%s)",
                type(response).__name__,
                llm_config.model,
                effective_max_tokens,
            )
            return None, truncated

        # glm 系推理模型的 thinking tokens 会计入 max_tokens,预算被 thinking 吃光时
        # 返回 HTTP 200 + 空 content。MiroFish 同端点的对策:去掉上限重试一次。
        content, truncated = await asyncio.wait_for(
            _do_call(max_tokens), timeout=timeout
        )
        if content and not truncated:
            return content
        logger.warning(
            "LLM %s with max_tokens=%s (source=%s), retrying without cap",
            "truncated" if truncated else "empty response",
            max_tokens,
            source,
        )
        retried, _ = await asyncio.wait_for(_do_call(None), timeout=timeout)
        return retried
    except asyncio.TimeoutError:
        logger.warning("LLM call timed out after %.1fs", timeout)
        return None
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return None

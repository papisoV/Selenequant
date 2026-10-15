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
) -> str | None:
    """Call the configured LLM with a prompt. Returns None if not configured or on error."""
    try:
        from selene_space_llm.base import LLMConfig, LLMMessage, LLMRole
        from selene_space_llm.openai_compat import OpenAICompatProvider

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

        async def _do_call(effective_max_tokens: int | None) -> str | None:
            response = await provider.chat(
                messages=llm_messages, max_tokens=effective_max_tokens
            )
            if response and isinstance(response, str):
                return response.strip()
            if response and hasattr(response, "content"):
                content: str = response.content
                return content.strip()
            logger.warning(
                "LLM returned empty/unexpected response (type=%s, model=%s, max_tokens=%s)",
                type(response).__name__,
                llm_config.model,
                effective_max_tokens,
            )
            return None

        # glm 系推理模型的 thinking tokens 会计入 max_tokens,预算被 thinking 吃光时
        # 返回 HTTP 200 + 空 content。MiroFish 同端点的对策:去掉上限重试一次。
        content = await asyncio.wait_for(_do_call(max_tokens), timeout=timeout)
        if content:
            return content
        logger.warning(
            "LLM empty response with max_tokens=%s, retrying without cap", max_tokens
        )
        return await asyncio.wait_for(_do_call(None), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("LLM call timed out after %.1fs", timeout)
        return None
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return None

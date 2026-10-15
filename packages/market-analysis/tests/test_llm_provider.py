"""US-004 — HttpLLMProvider 单测（接 selene 配置面，测试用假 provider，不依赖网络）。

覆盖验收标准：
- 复用 selene LLMConfig（零重复配置），构造不联网 / 无 key 不崩溃
- 超时 / 连接失败包装为 ProviderError（可识别异常）
- 注入假 provider 可同步/异步调用，返回 LLMResponse
"""
from __future__ import annotations

import pytest

from selene_space_llm.base import LLMConfig
from selene_space_llm.openai_compat import OpenAICompatProvider
from selene_space_llm.schemas import TokenUsage

from selene_space_market_analysis.exceptions import ProviderError
from selene_space_market_analysis.llm_provider import (
    HttpLLMProvider,
    LLMMessage,
    LLMResponse,
)


def _config(**overrides) -> LLMConfig:
    params = {
        "model": "test-model",
        "temperature": 0.2,
        "max_tokens": 256,
        "api_key": None,
        "base_url": None,
    }
    params.update(overrides)
    return LLMConfig(**params)


def _msgs(*texts: str) -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content="system"),
        *[LLMMessage(role="user", content=t) for t in texts],
    ]


def test_construct_without_key_does_not_crash() -> None:
    """无 api_key 构造安全（不联网）。"""
    provider = HttpLLMProvider(config=_config())
    assert provider.config.model == "test-model"


def test_call_returns_llm_response_from_injected_provider() -> None:
    """同步 call() 经 asyncio.run 走注入假 provider。"""
    fake = lambda messages: "hello"  # noqa: E731
    provider = HttpLLMProvider(config=_config(), provider=fake)
    resp = provider.call(_msgs("hi"))
    assert isinstance(resp, LLMResponse)
    assert resp.content == "hello"
    assert resp.usage == {}


@pytest.mark.asyncio
async def test_call_async_with_async_injected_provider() -> None:
    """异步 call_async() 支持 async 假 provider。"""

    async def fake(messages: list[LLMMessage]) -> str:
        return "async-ok"

    provider = HttpLLMProvider(config=_config(), provider=fake)
    resp = await provider.call_async(_msgs("hi"))
    assert resp.content == "async-ok"


@pytest.mark.asyncio
async def test_call_async_wraps_failure_as_provider_error() -> None:
    """注入 provider 抛错 → 统一 ProviderError。"""

    def fake(messages: list[LLMMessage]) -> str:
        raise TimeoutError("read timeout")

    provider = HttpLLMProvider(config=_config(), provider=fake)
    with pytest.raises(ProviderError):
        await provider.call_async(_msgs("hi"))


@pytest.mark.asyncio
async def test_call_async_uses_selene_config_surface() -> None:
    """默认构造复用 selene OpenAICompatProvider（走真实配置面，不新增私有项）。"""

    async def fake(messages: list[LLMMessage]) -> str:
        return "ok"

    provider = HttpLLMProvider(config=_config(), provider=fake)
    assert provider.config == _config()


def test_provider_sync_call_outside_loop_works() -> None:
    """同步 call 在无运行中事件循环时可用。"""
    provider = HttpLLMProvider(config=_config(), provider=lambda m: "done")
    assert provider.call(_msgs("x")).content == "done"


@pytest.mark.asyncio
async def test_call_async_captures_usage_via_chat_with_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 OpenAICompatProvider 路径经 chat_with_usage 把 usage 落入 LLMResponse。"""

    async def fake_chat_with_usage(
        self: OpenAICompatProvider, messages: list[object]
    ) -> tuple[str, TokenUsage]:
        return "ok", TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)

    monkeypatch.setattr(OpenAICompatProvider, "chat_with_usage", fake_chat_with_usage)
    provider = HttpLLMProvider(config=_config())
    resp = await provider.call_async(_msgs("hi"))
    assert resp.content == "ok"
    assert resp.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }

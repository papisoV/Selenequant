"""HttpLLMProvider — 接 selene 真实 LLM 配置面，适配决策式调用契约。

配置源零重复：直接复用 selene ``selene_space_llm.base.LLMConfig``（model /
api_key / base_url / temperature / max_tokens），不新增任何私有配置项。
底层复用 selene 的 OpenAICompatProvider（httpx、自带重试、SSRF 防护），
HTTP 出口收口到统一 OutboundGateway（线程隔离 + 每 host 熔断，外部网络
故障不再拖死事件循环）；失败包装为 ProviderError（可识别）。

agent-runtime 的 ``vibe_providers.py`` 是独立的 opt-in 列表面（VIBE_LLM_ENABLED
语义），与 HttpLLMProvider 解耦，不在此引入依赖。
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import httpx

from selene_space_core.resilience import OutboundGateway
from selene_space_llm.base import LLMConfig, LLMError
from selene_space_llm.openai_compat import OpenAICompatProvider

from .exceptions import ProviderError


@dataclass(frozen=True)
class LLMMessage:
    """单条消息（决策式调用契约，市场侧自拟）。"""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMResponse:
    """单次 LLM 响应（决策式调用契约）。"""

    content: str
    reasoning_content: str | None
    usage: dict[str, int]


_MSG_ROLES = frozenset({"system", "user", "assistant"})


class HttpLLMProvider:
    """基于 selene OpenAICompatProvider 的 HTTP LLM 提供方。

    - 主方法 ``call_async``：异步调用（FastAPI / 编排器内使用）。
    - ``call``：同步便捷包装（``asyncio.run``），供脚本 / 单测使用；
      已在运行中的事件循环内调用会抛 RuntimeError（改用 call_async）。

    构造不联网：无 api_key / base_url 也安全（OpenAICompatProvider 对
    api_key=None 已容错）。测试可注入假 provider，不依赖网络。
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        provider: Callable[[list[LLMMessage]], Awaitable[str] | str] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self._config = config
        self._timeout = timeout or httpx.Timeout(
            connect=10.0, read=60.0, write=30.0, pool=10.0
        )
        if provider is None:
            # 复用 selene 原生 provider；HTTP 出口收口到统一 OutboundGateway
            # （线程隔离 + 每 host 熔断）。外部 LLM 故障不会拖死事件循环。
            self._client = OutboundGateway(
                default_timeout=self._timeout,
                default_circuit=True,
            )
            self._inner = OpenAICompatProvider(config, client=self._client)
        else:
            self._client = None
            self._inner = provider

    @property
    def config(self) -> LLMConfig:
        """底层 selene 配置（只读）。"""
        return self._config

    async def call_async(self, messages: list[LLMMessage]) -> LLMResponse:
        """异步调用 LLM。

        超时 / 连接失败 / API 错误统一包装为 ProviderError。
        """
        if isinstance(self._inner, OpenAICompatProvider):
            try:
                text, usage = await self._inner.chat_with_usage(
                    self._to_selene_messages(messages)
                )
            except (httpx.HTTPError, LLMError, TimeoutError) as exc:
                raise ProviderError(
                    f"LLM 调用失败: {exc}",
                    cause=exc if isinstance(exc, Exception) else None,
                ) from exc
            return LLMResponse(
                content=text,
                reasoning_content=None,
                usage={
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                },
            )

        # 注入的假 provider（测试用）：同步或异步皆可。
        try:
            result = self._inner(messages)
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as exc:
            raise ProviderError(f"LLM 调用失败: {exc}", cause=exc) from exc
        return LLMResponse(content=str(result), reasoning_content=None, usage={})

    @property
    def supports_stream(self) -> bool:
        """是否支持流式输出（仅底层 OpenAICompatProvider 提供 chat_stream）。"""
        return isinstance(self._inner, OpenAICompatProvider)

    async def stream(self, messages: list[LLMMessage]) -> AsyncIterator[str]:
        """流式调用 LLM，逐 chunk 产出文本片段。

        仅当 :attr:`supports_stream` 为 True 时可用；假 provider（无流式能力）
        抛 ProviderError，编排层据此退化到 call_async。
        """
        if not isinstance(self._inner, OpenAICompatProvider):
            raise ProviderError("provider does not support streaming")
        try:
            async for chunk in self._inner.chat_stream(self._to_selene_messages(messages)):
                yield chunk
        except (httpx.HTTPError, LLMError, TimeoutError) as exc:
            raise ProviderError(
                f"LLM 流式调用失败: {exc}",
                cause=exc if isinstance(exc, Exception) else None,
            ) from exc

    def _to_selene_messages(self, messages: list[LLMMessage]) -> list:
        from selene_space_llm.base import LLMMessage as SeleneMessage
        from selene_space_llm.base import LLMRole

        out: list = []
        for msg in messages:
            role = getattr(msg, "role", None)
            role_enum = LLMRole(role) if isinstance(role, str) and role in _MSG_ROLES else LLMRole.USER
            out.append(SeleneMessage(role=role_enum, content=getattr(msg, "content", "")))
        return out

    def call(self, messages: list[LLMMessage]) -> LLMResponse:
        """同步便捷包装（asyncio.run）。已在事件循环内调用会抛 RuntimeError。"""
        return asyncio.run(self.call_async(messages))

    async def close(self) -> None:
        """关闭持有的 httpx client（仅当本类自建时）。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = ["LLMMessage", "LLMResponse", "HttpLLMProvider"]

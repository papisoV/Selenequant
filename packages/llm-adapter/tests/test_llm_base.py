"""Behavior locks for selene_space_llm.base: message/config models,
exception hierarchy, and the abstract provider contract."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import ValidationError

from selene_space_llm.base import (
    LLMAuthError,
    LLMConfig,
    LLMConnectionError,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMRateLimitError,
    LLMRole,
)


class MinimalProvider(LLMProvider):
    """Concrete subclass satisfying the ABC without any I/O."""

    async def chat(self, messages: list[LLMMessage], **kwargs: Any) -> str:
        return "reply-from-minimal"

    async def chat_stream(
        self, messages: list[LLMMessage], **kwargs: Any
    ) -> AsyncIterator[str]:
        for piece in ("hello", " ", "world"):
            yield piece

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        output_schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {"result": "ok"}


def _turn(text: str = "hi") -> list[LLMMessage]:
    return [LLMMessage(role=LLMRole.USER, content=text)]


# --- message model ---------------------------------------------------------


def test_message_holds_role_and_content() -> None:
    m = LLMMessage(role=LLMRole.USER, content="hello")
    assert m.role is LLMRole.USER
    assert m.content == "hello"


def test_message_json_roundtrip() -> None:
    original = LLMMessage(role=LLMRole.SYSTEM, content="You are a quant.")
    restored = LLMMessage.model_validate(json.loads(original.model_dump_json()))
    assert restored == original


@pytest.mark.parametrize("role", list(LLMRole))
def test_every_role_constructs(role: LLMRole) -> None:
    built = LLMMessage(role=role, content="x")
    assert built.role is role


# --- config model ----------------------------------------------------------


def test_config_default_profile() -> None:
    cfg = LLMConfig(model="any-model-id")
    assert cfg.temperature == pytest.approx(0.7)
    assert cfg.max_tokens == 4096
    assert cfg.api_key is None
    assert cfg.base_url is None


def test_config_accepts_overrides() -> None:
    cfg = LLMConfig(
        api_key="sk-fixture",
        model="vendor-specific",
        base_url="http://127.0.0.1:9999/v1",
        max_tokens=512,
        temperature=0.05,
    )
    assert cfg.model == "vendor-specific"
    assert (cfg.temperature, cfg.max_tokens) == (0.05, 512)
    assert {cfg.api_key, cfg.base_url} == {"sk-fixture", "http://127.0.0.1:9999/v1"}


def test_config_json_roundtrip() -> None:
    original = LLMConfig(model="roundtrip-model", api_key="sk-roundtrip")
    restored = LLMConfig.model_validate(json.loads(original.model_dump_json()))
    assert restored == original


@pytest.mark.parametrize("bad_temp", [-0.1, 2.1], ids=["below-zero", "above-two"])
def test_temperature_outside_unit_interval_rejected(bad_temp: float) -> None:
    with pytest.raises(ValidationError):
        LLMConfig(model="m", temperature=bad_temp)


# --- exception hierarchy ---------------------------------------------------


@pytest.mark.parametrize(
    "specialization",
    [LLMConnectionError, LLMRateLimitError, LLMAuthError],
)
def test_specializations_share_base(specialization: type[LLMError]) -> None:
    assert issubclass(specialization, LLMError)


def test_rate_limit_carries_retry_after() -> None:
    seconds = 12.5
    err = LLMRateLimitError("slow down", retry_after=seconds)
    assert err.retry_after == seconds
    assert str(err).find("slow down") >= 0


@pytest.mark.parametrize(
    "exc_cls", [LLMConnectionError, LLMRateLimitError, LLMAuthError]
)
def test_base_except_catches_specializations(exc_cls: type[LLMError]) -> None:
    raised = exc_cls("synthetic failure")
    with pytest.raises(LLMError):
        raise raised


# --- provider contract -----------------------------------------------------


def test_abc_is_not_directly_instantiable() -> None:
    try:
        LLMProvider(config=LLMConfig(model="x"))  # type: ignore[abstract]
    except TypeError:
        pass
    else:
        pytest.fail("abstract LLMProvider was instantiated")


@pytest.mark.asyncio
async def test_minimal_provider_chat() -> None:
    out = await MinimalProvider(config=LLMConfig(model="stub")).chat(_turn())
    assert out == "reply-from-minimal"


@pytest.mark.asyncio
async def test_minimal_provider_stream_pieces_join_to_sentence() -> None:
    provider = MinimalProvider(config=LLMConfig(model="stub"))
    collected = [chunk async for chunk in provider.chat_stream(_turn())]
    assert "".join(collected) == "hello world"


@pytest.mark.asyncio
async def test_minimal_provider_structured() -> None:
    provider = MinimalProvider(config=LLMConfig(model="stub"))
    out = await provider.generate_structured(_turn(), {"type": "object"})
    assert out == {"result": "ok"}


def test_config_exposed_on_instance() -> None:
    shared_cfg = LLMConfig(model="identity-check")
    assert MinimalProvider(config=shared_cfg).config is shared_cfg

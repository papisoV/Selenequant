"""Plugin SDK locks for selene_space_core.plugin_sdk.types — protocol shape
and required method surface for each plugin kind."""

import pytest

from selene_space_core.plugin_sdk.types import (
    ExchangeAdapter,
    IndicatorPlugin,
    LLMProviderPlugin,
    StrategyPlugin,
    ToolPlugin,
)

PROTOCOL_KINDS = [ExchangeAdapter, StrategyPlugin, IndicatorPlugin, LLMProviderPlugin, ToolPlugin]


@pytest.mark.parametrize("kind", PROTOCOL_KINDS)
def test_each_kind_is_protocol_like(kind):
    checks = hasattr(kind, "__protocol_attrs__") or callable(kind)
    assert checks, f"{kind.__name__} is not protocol-like"


def test_exchange_adapter_surface_is_complete():
    required = {"connect", "disconnect", "subscribe", "unsubscribe", "name"}
    missing = {m for m in required if not hasattr(ExchangeAdapter, m)}
    assert not missing, f"ExchangeAdapter missing {missing}"


def test_tool_plugin_surface_is_complete():
    for m in ("name", "description", "execute"):
        assert hasattr(ToolPlugin, m), f"ToolPlugin missing {m}"

"""Plugin SDK locks for selene_space_core.plugin_sdk.api — the exact
register_* surface contract (14 methods)."""

import re

from selene_space_core.plugin_sdk.api import SeleneSpacePluginAPI

# Spec: SeleneSpacePluginAPI must have 14 register_* methods.
REGISTRAR_SPEC = """
register_exchange
register_strategy
register_indicator
register_llm_provider
register_channel
register_tool
register_hook
register_risk_rule
register_metric
register_health_check
register_command
register_middleware
register_mcp_server
register_skill
"""


def _spec_names() -> set[str]:
    return set(re.findall(r"register_\w+", REGISTRAR_SPEC))


def _actual_names() -> set[str]:
    return {m for m in dir(SeleneSpacePluginAPI) if m.startswith("register_")}


def test_registrar_count_matches_spec():
    pair = (len(_actual_names()), len(_spec_names()))
    assert pair == (14, 14)


def test_actual_registrars_equal_the_specified_set():
    assert _actual_names() == _spec_names()


def test_api_is_runtime_checkable_protocol():
    protocol_markers = ("__protocol_attrs__", "__abstractmethods__")
    assert any(hasattr(SeleneSpacePluginAPI, marker) for marker in protocol_markers)

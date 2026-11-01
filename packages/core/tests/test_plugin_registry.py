"""Plugin SDK locks for selene_space_core.plugin_sdk.registry — singleton
identity, namespaced registration, duplicate rejection, listing, reset."""

import pytest

from selene_space_core.plugin_sdk.registry import PluginRegistry


@pytest.fixture(autouse=True)
def fresh_registry():
    PluginRegistry.reset()
    yield


def test_registry_is_a_singleton():
    assert PluginRegistry() is PluginRegistry()


def test_registration_then_lookup_roundtrips():
    reg = PluginRegistry()
    reg.register("exchange", "okx", {"adapter": True})
    assert reg.get("exchange", "okx") == {"adapter": True}


def test_unknown_name_lookups_return_none():
    assert PluginRegistry().get("exchange", "phantom") is None


def test_double_registration_is_rejected():
    reg = PluginRegistry()
    reg.register("exchange", "okx", {})
    with pytest.raises(ValueError, match="already registered"):
        reg.register("exchange", "okx", {})


def test_list_returns_every_name_in_namespace():
    reg = PluginRegistry()
    for name in ("sma", "ema"):
        reg.register("indicator", name, {"kind": name})
    found = reg.list("indicator")
    checks = (len(found) == 2, "sma" in found, "ema" in found)
    assert all(checks)


def test_capabilities_aggregate_across_namespaces():
    reg = PluginRegistry()
    reg.register("exchange", "okx", {})
    reg.register("indicator", "sma", {})
    caps = reg.list_capabilities()
    checks = ("exchange" in caps, "indicator" in caps)
    assert all(checks)


def test_clear_wipes_all_namespaces():
    reg = PluginRegistry()
    reg.register("exchange", "okx", {})
    reg.clear()
    assert reg.get("exchange", "okx") is None

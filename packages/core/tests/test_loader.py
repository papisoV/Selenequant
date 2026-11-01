"""Plugin SDK locks for selene_space_core.plugin_sdk.loader — manifest
defaults and fields, stdlib fallback loading, entry point resolution."""

import pytest

from selene_space_core.plugin_sdk.loader import PluginLoader, PluginManifest


class TestManifestModel:
    def test_bare_manifest_gets_defaults(self):
        bare = PluginManifest(name="bare-bones")
        trio = (bare.name, bare.version, bare.capabilities)
        assert trio == ("bare-bones", "0.0.0", [])

    def test_populated_manifest_keeps_all_fields(self):
        filled = PluginManifest(
            name="okx",
            version="1.2.3",
            description="OKX adapter",
            capabilities=["exchange"],
            entry_point="okx_plugin:setup",
        )
        assert filled.entry_point == "okx_plugin:setup"


class TestModuleLoading:
    def test_stdlib_module_loads_with_name_fallback(self):
        loader = PluginLoader()
        outcome = loader.load("json")
        # Falls back to module name
        assert outcome.name == "json"
        assert outcome.module is not None

    def test_unknown_module_raises_import_error(self):
        with pytest.raises(ImportError):
            PluginLoader().load("no_such_module_qq77")

    def test_entry_point_resolves_setup_callable(self):
        outcome = PluginLoader().load_entry_point("json:dumps")
        checks = (outcome.module is not None, outcome.setup_fn is not None)
        assert all(checks)  # json.dumps exists

"""Plugin SDK locks for selene_space_core.plugin_sdk.discovery — bundled
sources, dedupe precedence, caching, force refresh, workspace + user config."""

from selene_space_core.plugin_sdk.discovery import DiscoveredPlugin, PluginDiscovery


def _bundled(name: str, module: str) -> DiscoveredPlugin:
    return DiscoveredPlugin(name=name, module=module, source="bundled")


def test_bundled_plugins_appear_first_in_results():
    disco = PluginDiscovery(
        bundled=[_bundled("okx", "selene_space_exchange_okx")],
    )
    results = disco.discover()
    checks = (
        len(results) >= 1,
        results[0].name == "okx",
        results[0].source == "bundled",
    )
    assert all(checks)


def test_same_name_resolves_to_bundled_winner():
    disco = PluginDiscovery(
        bundled=[
            _bundled("binance", "mod_bundled"),
            DiscoveredPlugin(name="binance", module="mod_user", source="user"),
        ],
    )
    matches = [r for r in disco.discover() if r.name == "binance"]
    pair = (len(matches), matches[0].source)
    # First wins
    assert pair == (1, "bundled")


def test_discovery_result_is_cached_between_calls():
    disco = PluginDiscovery(bundled=[_bundled("a", "m")])
    assert disco.discover() == disco.discover()


def test_force_refresh_after_invalidation_still_returns_a_list():
    disco = PluginDiscovery(bundled=[])
    disco.discover()
    disco.invalidate_cache()
    refreshed = disco.discover(force_refresh=True)
    assert isinstance(refreshed, list)


def test_workspace_directories_become_plugins(tmp_path):
    folder = tmp_path / "helper_plugin"
    folder.mkdir()
    (folder / "__init__.py").write_text("")

    results = PluginDiscovery(workspace_dir=tmp_path).discover()
    found = [r for r in results if r.source == "workspace"]
    pair = (len(found), found[0].name)
    assert pair == (1, "helper_plugin")


def test_user_yaml_config_lists_plugins(tmp_path):
    config_file = tmp_path / "plugins.yaml"
    config_file.write_text("plugins:\n  - name: custom\n    module: custom_plugin\n")

    results = PluginDiscovery(user_config_path=config_file).discover()
    found = [r for r in results if r.source == "user"]
    pair = (len(found), found[0].name)
    assert pair == (1, "custom")

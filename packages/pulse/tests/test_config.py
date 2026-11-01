from __future__ import annotations

import pytest

from ai_market_pulse.config import (
    AppConfig,
    _expand_env,
    _filter_dataclass,
    _parse_asset,
    load_config,
    load_config_from_mapping,
)
from ai_market_pulse.models import Asset


def test_expand_env_with_default_uses_env_value_when_set(monkeypatch) -> None:
    monkeypatch.setenv("MP_TEST_VAR", "actual-value")

    result = _expand_env("${MP_TEST_VAR:-fallback}")

    assert result == "actual-value"


def test_expand_env_with_default_uses_default_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("MP_TEST_VAR", raising=False)

    result = _expand_env("${MP_TEST_VAR:-fallback}")

    assert result == "fallback"


def test_expand_env_plain_var_uses_env_value_when_set(monkeypatch) -> None:
    monkeypatch.setenv("MP_TEST_VAR", "plain-value")

    result = _expand_env("${MP_TEST_VAR}")

    assert result == "plain-value"


def test_expand_env_plain_var_resolves_to_empty_string_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("MP_TEST_VAR", raising=False)

    result = _expand_env("${MP_TEST_VAR}")

    assert result == ""


def test_expand_env_recurses_into_dicts_and_lists(monkeypatch) -> None:
    monkeypatch.setenv("MP_TEST_VAR", "nested-value")

    result = _expand_env(
        {
            "a": "${MP_TEST_VAR}",
            "b": ["${MP_TEST_VAR:-x}", 1, None],
        }
    )

    assert result == {"a": "nested-value", "b": ["nested-value", 1, None]}


def test_expand_env_leaves_non_string_scalars_unchanged() -> None:
    assert _expand_env(42) == 42
    assert _expand_env(None) is None
    assert _expand_env(True) is True


def _minimal_mapping() -> dict:
    return {
        "title": "My Pulse",
        "timezone": "UTC",
        "assets": [{"symbol": "AAPL"}],
    }


def test_load_config_from_mapping_minimal_valid_mapping_produces_expected_config() -> None:
    config = load_config_from_mapping(_minimal_mapping())

    assert isinstance(config, AppConfig)
    assert config.title == "My Pulse"
    assert config.timezone == "UTC"
    assert config.assets == [Asset(symbol="AAPL")]
    assert config.notifications == []


def test_load_config_from_mapping_applies_defaults_for_optional_sections() -> None:
    config = load_config_from_mapping(_minimal_mapping())

    assert config.analysis.lookback_days == 220
    assert config.data.providers == ["akshare", "yfinance"]
    assert config.benchmarks.enabled is True
    assert config.news.language == "en-US"
    assert config.llm.enabled is False


def test_load_config_from_mapping_zero_assets_raises_value_error() -> None:
    with pytest.raises(ValueError, match="at least one asset"):
        load_config_from_mapping({"title": "Empty", "timezone": "UTC", "assets": []})


def test_load_config_from_mapping_missing_assets_key_raises_value_error() -> None:
    with pytest.raises(ValueError, match="at least one asset"):
        load_config_from_mapping({"title": "Empty", "timezone": "UTC"})


def test_load_config_from_mapping_expands_env_vars(monkeypatch) -> None:
    monkeypatch.setenv("MP_TITLE", "Env Title")

    config = load_config_from_mapping(
        {
            "title": "${MP_TITLE}",
            "timezone": "${MP_TZ:-America/New_York}",
            "assets": [{"symbol": "MSFT"}],
        }
    )

    assert config.title == "Env Title"
    assert config.timezone == "America/New_York"


def test_load_config_reads_yaml_file_from_disk(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "title: File Pulse\n"
        "timezone: UTC\n"
        "assets:\n"
        "  - symbol: GOOG\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.title == "File Pulse"
    assert config.assets == [Asset(symbol="GOOG")]


def test_parse_asset_bare_string_defaults_market_to_us() -> None:
    asset = _parse_asset("TSLA")

    assert asset == Asset(symbol="TSLA")
    assert asset.market == "US"
    assert asset.name is None
    assert asset.tags == []
    assert asset.quantity is None
    assert asset.cost_basis is None
    assert asset.note is None


def test_parse_asset_full_dict_entry_produces_correct_asset() -> None:
    asset = _parse_asset(
        {
            "symbol": "NVDA",
            "name": "NVIDIA Corp",
            "market": "US",
            "currency": "USD",
            "quantity": 10,
            "cost_basis": 120.5,
            "tags": ["ai", "semis"],
            "note": "core holding",
        }
    )

    assert asset == Asset(
        symbol="NVDA",
        name="NVIDIA Corp",
        market="US",
        currency="USD",
        tags=["ai", "semis"],
        quantity=10,
        cost_basis=120.5,
        note="core holding",
    )


def test_parse_asset_dict_entry_without_market_defaults_to_us() -> None:
    asset = _parse_asset({"symbol": "000001.SZ", "name": "Ping An"})

    assert asset.market == "US"


def test_parse_asset_dict_entry_with_explicit_non_us_market() -> None:
    asset = _parse_asset({"symbol": "0700.HK", "market": "HK"})

    assert asset.market == "HK"


def test_parse_asset_dict_entry_missing_tags_defaults_to_empty_list() -> None:
    asset = _parse_asset({"symbol": "AAPL"})

    assert asset.tags == []


def test_filter_dataclass_drops_unknown_keys_and_keeps_known_ones() -> None:
    from ai_market_pulse.config import NewsSettings

    filtered = _filter_dataclass(NewsSettings, {"language": "zh-CN", "bogus_key": "ignored"})

    assert filtered == {"language": "zh-CN"}


def test_filter_dataclass_empty_input_returns_empty_dict() -> None:
    from ai_market_pulse.config import NewsSettings

    assert _filter_dataclass(NewsSettings, {}) == {}

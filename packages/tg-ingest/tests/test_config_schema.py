"""Tests for config_schema advisory typo detection (tgwatcher absorption Step 3)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))


def test_valid_config_no_warnings():
    from selene_space_tg_ingest.config_schema import validate_config

    cfg = {
        "storage": {"db_path": "./data/tgwatcher.db"},
        "groups": [{"id": 0, "name": "Test", "username": "t"}],
        "signal": {
            "enabled": False,
            "llm": {
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "",
                "model": "deepseek-chat",
                "providers": {"anthropic": {"api_key": "", "model": "claude-sonnet-5"}},
                "fallback_order": ["deepseek", "anthropic"],
            },
            "filter": {"min_text_length": 10},
            "keywords": {},
        },
        "timezone": {"utc_offset_hours": 8},
    }
    assert validate_config(cfg) == []


def test_known_typo_flagged():
    from selene_space_tg_ingest.config_schema import validate_config

    cfg = {"signal": {"llm": {"max_tokens_btach": 512}}}
    warnings = validate_config(cfg)
    assert any("max_tokens_btach" in w and "max_tokens_batch" in w for w in warnings)


def test_close_match_typo_flagged():
    from selene_space_tg_ingest.config_schema import validate_config

    cfg = {"signal": {"llm": {"timeout_red": 30}}}
    warnings = validate_config(cfg)
    assert any("timeout_red" in w for w in warnings)


def test_unknown_key_not_flagged_as_false_positive():
    """Keys not in the allowlist must NOT be flagged when they're legitimate
    tg-ingest schema (e.g. flat legacy llm fields at signal.llm level)."""
    from selene_space_tg_ingest.config_schema import validate_config

    cfg = {"signal": {"llm": {"base_url": "x", "api_key": "", "model": "m",
                              "temperature": 0, "max_tokens": 512}}}
    assert validate_config(cfg) == []


def test_example_config_yields_no_warnings():
    """config.example.yaml must parse clean — zero typo warnings."""
    from selene_space_tg_ingest.config_schema import validate_config
    from selene_space_tg_ingest.config import DEFAULT_EXAMPLE_YAML
    import yaml

    raw = yaml.safe_load(DEFAULT_EXAMPLE_YAML.read_text(encoding="utf-8"))
    assert validate_config(raw) == []


def test_non_dict_config_no_crash():
    from selene_space_tg_ingest.config_schema import validate_config

    assert validate_config([]) == []  # type: ignore[arg-type]
    assert validate_config("nope") == []  # type: ignore[arg-type]

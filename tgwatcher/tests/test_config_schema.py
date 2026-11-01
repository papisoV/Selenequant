"""Tests for tgwatcher.config_schema.validate_config."""
from __future__ import annotations

import logging


from tgwatcher.config_schema import validate_config


def _valid_config() -> dict:
    return {
        "storage": {"db_path": "./data/tgwatcher.db"},
        "groups": [{"id": 1, "name": "g1", "username": "ch1"}],
        "signal": {
            "enabled": False,
            "llm": {
                "provider": "deepseek",
                "providers": {
                    "deepseek": {
                        "api_key": "k",
                        "base_url": "https://api.deepseek.com/v1",
                        "model": "deepseek-chat",
                    },
                },
            },
        },
    }


class TestValidateConfig:
    def test_valid_config_passes(self):
        assert validate_config(_valid_config()) == []

    def test_missing_required_key_fails(self):
        cfg = _valid_config()
        del cfg["storage"]
        errors = validate_config(cfg)
        assert any("storage" in e for e in errors)

    def test_typo_produces_warning_not_error(self, caplog):
        cfg = _valid_config()
        cfg["signal"]["llm"]["providers"]["deepseek"]["max_tokens_btach"] = 1024
        with caplog.at_level(logging.WARNING, logger="tgwatcher.config_schema"):
            errors = validate_config(cfg)
        assert errors == []
        assert any("max_tokens_btach" in r.message for r in caplog.records)

    def test_invalid_active_provider_fails(self):
        cfg = _valid_config()
        cfg["signal"]["llm"]["provider"] = "nonexistent"
        errors = validate_config(cfg)
        assert any("nonexistent" in e and "providers" in e for e in errors)

    def test_signal_enabled_requires_provider_credentials(self):
        cfg = _valid_config()
        cfg["signal"]["enabled"] = True
        del cfg["signal"]["llm"]["providers"]["deepseek"]["api_key"]
        errors = validate_config(cfg)
        assert any("api_key" in e for e in errors)

    def test_groups_invalid_id_type_fails(self):
        cfg = _valid_config()
        cfg["groups"][0]["id"] = "not-an-int"
        errors = validate_config(cfg)
        assert any("id" in e for e in errors)

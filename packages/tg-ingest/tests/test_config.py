"""Tests for the absorbed multi-provider LLM config (tgwatcher 2026-08-10).

Covers:
- TgIngestConfig.load parses signal.llm.{provider,providers,fallback_order}
- to_runtime_dict carries the pool through to runtime["signal"]["llm"]
- LLMConfig.from_dict resolves providers pool vs legacy flat config
- unknown provider / bad fallback_order raise ValueError
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))

from selene_space_tg_ingest.config import TgIngestConfig  # noqa: E402
from selene_space_tg_ingest.signal_llm import LLMConfig, SignalLLMClient  # noqa: E402

_POOL_YAML = """\
signal:
  enabled: true
  llm:
    provider: deepseek
    providers:
      deepseek:
        base_url: https://api.deepseek.com/v1
        api_key: test-key
        model: deepseek-chat
      anthropic:
        base_url: https://api.anthropic.com
        api_key: test-key-2
        model: claude-sonnet-5
    fallback_order: [deepseek, anthropic]
  filter:
    min_text_length: 10
"""


def _write_yaml(tmp_path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(content, encoding="utf-8")
    return p


# ---------- TgIngestConfig parsing ----------

def test_config_load_parses_provider_fields(tmp_path):
    cfg = TgIngestConfig.load(_write_yaml(tmp_path, _POOL_YAML))
    assert cfg.llm_provider == "deepseek"
    assert set(cfg.llm_providers) == {"deepseek", "anthropic"}
    assert cfg.llm_fallback_order == ["deepseek", "anthropic"]


def test_config_to_runtime_dict_carries_pool(tmp_path):
    cfg = TgIngestConfig.load(_write_yaml(tmp_path, _POOL_YAML))
    runtime = cfg.to_runtime_dict()
    llm = runtime["signal"]["llm"]
    assert llm["provider"] == "deepseek"
    assert llm["fallback_order"] == ["deepseek", "anthropic"]
    assert llm["providers"]["deepseek"]["base_url"] == "https://api.deepseek.com/v1"


def test_config_legacy_flat_defaults_openai(tmp_path):
    yaml = """\
signal:
  enabled: true
  llm:
    base_url: https://api.deepseek.com/v1
    model: deepseek-chat
    api_key: key
"""
    cfg = TgIngestConfig.load(_write_yaml(tmp_path, yaml))
    assert cfg.llm_provider == "openai"  # default when no provider key
    assert cfg.llm_providers == {}
    assert cfg.llm_fallback_order == []


# ---------- LLMConfig.from_dict ----------

def test_from_dict_providers_pool(monkeypatch):
    monkeypatch.delenv("SELENE_TG_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SIGNAL_LLM_API_KEY", raising=False)
    cfg = LLMConfig.from_dict({
        "provider": "deepseek",
        "providers": {
            "deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key": "k1", "model": "deepseek-chat"},
            "anthropic": {"base_url": "https://api.anthropic.com", "api_key": "k2", "model": "claude-sonnet-5"},
        },
        "fallback_order": ["deepseek", "anthropic"],
    })
    assert cfg.provider == "deepseek"
    assert cfg.base_url == "https://api.deepseek.com/v1"
    assert cfg.model == "deepseek-chat"
    assert cfg.fallback_order == ["deepseek", "anthropic"]


def test_from_dict_legacy_flat(monkeypatch):
    monkeypatch.delenv("SELENE_TG_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SIGNAL_LLM_API_KEY", raising=False)
    cfg = LLMConfig.from_dict({"base_url": "https://legacy/v1", "model": "legacy-model", "api_key": "k"})
    assert cfg.provider == "openai"
    assert cfg.base_url == "https://legacy/v1"
    assert cfg.model == "legacy-model"
    assert cfg.fallback_order == ["openai"]


def test_from_dict_unknown_provider_raises():
    with pytest.raises(ValueError):
        LLMConfig.from_dict({"provider": "not-a-provider", "providers": {"not-a-provider": {"base_url": "x"}}})


def test_from_dict_unknown_in_fallback_raises():
    with pytest.raises(ValueError):
        LLMConfig.from_dict({
            "base_url": "https://x",
            "model": "m",
            "fallback_order": ["openai", "not-a-provider"],
        })


def test_from_dict_empty_fallback_raises():
    with pytest.raises(ValueError):
        LLMConfig.from_dict({"base_url": "https://x", "model": "m", "fallback_order": []})


def test_from_dict_missing_base_url_raises():
    with pytest.raises(ValueError):
        LLMConfig.from_dict({"provider": "openai", "providers": {"openai": {"model": "m"}}})


# ---------- SignalLLMClient pool construction ----------

def test_client_constructs_from_pool_config(monkeypatch):
    """Building the client from a providers-dict config must not raise (no network)."""
    monkeypatch.delenv("SELENE_TG_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SIGNAL_LLM_API_KEY", raising=False)
    cfg = LLMConfig.from_dict({
        "provider": "deepseek",
        "providers": {
            "deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key": "k", "model": "deepseek-chat"},
        },
        "fallback_order": ["deepseek"],
    })
    client = SignalLLMClient(cfg)
    assert client.provider == "deepseek"
    assert client.model_name == "deepseek-chat"

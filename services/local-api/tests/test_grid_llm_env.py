"""H17 — grid.py LLM env var name test.

Asserts that _create_llm_provider reads SELENE_LLM_* env vars (not generic
AI_*), with backward-compat fallback to AI_* (deprecated, logged).
"""

from __future__ import annotations


import pytest


def _has_grid_module() -> bool:
    try:
        from app.api.v1 import grid  # noqa: F401
        return True
    except Exception:
        return False


_HAS_GRID = _has_grid_module()


@pytest.mark.skipif(not _HAS_GRID, reason="app.api.v1.grid import requires full dep graph")
class TestGridLlmEnvVars:
    def test_selene_llm_api_key_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SELENE_LLM_API_KEY", "selene-key")
        monkeypatch.setenv("SELENE_LLM_BASE_URL", "https://selene.example.com/v1")
        monkeypatch.setenv("SELENE_LLM_MODEL", "claude-opus-4-5")
        monkeypatch.delenv("AI_API_KEY", raising=False)
        monkeypatch.delenv("AI_BASE_URL", raising=False)
        monkeypatch.delenv("AI_MODEL", raising=False)

        from app.api.v1.grid import _create_llm_provider

        provider = _create_llm_provider()
        assert provider is not None
        # Provider config — inspect via the LLMConfig passed in.
        # OpenAICompatProvider stores config; access depends on impl.
        cfg = getattr(provider, "_config", None) or getattr(provider, "config", None)
        if cfg is not None:
            assert cfg.api_key == "selene-key"
            assert cfg.base_url == "https://selene.example.com/v1"
            assert cfg.model == "claude-opus-4-5"

    def test_ai_fallback_backward_compat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AI_* vars still work (deprecated) when SELENE_LLM_* not set."""
        monkeypatch.delenv("SELENE_LLM_API_KEY", raising=False)
        monkeypatch.delenv("SELENE_LLM_BASE_URL", raising=False)
        monkeypatch.delenv("SELENE_LLM_MODEL", raising=False)
        monkeypatch.setenv("AI_API_KEY", "legacy-key")
        monkeypatch.setenv("AI_BASE_URL", "https://legacy.example.com/v1")
        monkeypatch.setenv("AI_MODEL", "gpt-4o-mini")

        from app.api.v1.grid import _create_llm_provider

        provider = _create_llm_provider()
        assert provider is not None

    def test_default_model_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no model in env, the model is left to the endpoint ("default").

        It used to fall back to a pinned cloud model name. That fabricated a
        pairing -- whichever base_url was configured got a model that endpoint
        does not serve -- so every call failed with a mismatch the user had no
        way to read.
        """
        monkeypatch.delenv("SELENE_LLM_MODEL", raising=False)
        monkeypatch.delenv("AI_MODEL", raising=False)
        monkeypatch.setenv("SELENE_LLM_API_KEY", "k")
        monkeypatch.delenv("AI_API_KEY", raising=False)
        monkeypatch.delenv("SELENE_LLM_BASE_URL", raising=False)
        monkeypatch.delenv("AI_BASE_URL", raising=False)

        from app.api.v1.grid import _create_llm_provider

        provider = _create_llm_provider()
        assert provider is not None
        assert provider.config.model == "default", (
            "no model configured must not be answered with a fabricated one"
        )

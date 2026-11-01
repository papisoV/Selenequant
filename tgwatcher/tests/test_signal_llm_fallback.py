"""Tests for SignalLLMClient fallback chain (provider failover on transient errors).

Covers:
- LLMConfig.fallback_order default and explicit parsing
- 503/timeout/connection-error trigger fallback to next provider
- All-providers-exhausted raises last error
- Permanent errors (400 BadRequest) do NOT trigger fallback
- Dead providers (failed construction) are skipped, not retried
- Unknown provider in fallback_order raises at config load
- Non-transient errors are re-raised, not swallowed
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tgwatcher.signal_llm import LLMConfig, SignalLLMClient


def _make_llm_config(
    provider: str = "openai",
    fallback_order: list[str] | None = None,
    providers: dict | None = None,
) -> LLMConfig:
    """Build an LLMConfig with sensible defaults for tests.

    Default providers dict includes openai/anthropic/astron with placeholder
    credentials so fallback_order can reference any of them.
    """
    if providers is None:
        providers = {
            "openai": {
                "api_key": "sk-openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
            },
            "anthropic": {
                "api_key": "sk-anthropic",
                "base_url": "https://api.anthropic.com",
                "model": "claude-sonnet-4-6",
            },
            "astron": {
                "api_key": "sk-astron",
                "base_url": "https://maas-coding-api.example.com/v1",
                "model": "astron-code-latest",
            },
        }
    llm_cfg = {
        "provider": provider,
        "providers": providers,
        "fallback_order": fallback_order or [provider],
    }
    return LLMConfig.from_dict(llm_cfg)


def _make_mock_client(response_text: str = "ok") -> MagicMock:
    """Build a MagicMock client whose chat.completions.create returns a
    canned response with the given text.
    """
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = response_text
    response = MagicMock()
    response.choices = [choice]
    response.usage = None
    client.chat.completions.create.return_value = response
    return client


def _make_error_with_status(status: int, message: str = "fail") -> Exception:
    """Build a plain Exception carrying a status_code attribute (duck-typed)."""
    e = Exception(message)
    e.status_code = status  # type: ignore[attr-defined]
    return e


class TestFallbackOrderParsing:
    def test_fallback_order_default_is_primary(self):
        """LLMConfig without fallback_order has [provider] as default."""
        cfg = _make_llm_config(provider="openai", fallback_order=None)
        assert cfg.fallback_order == ["openai"]

    def test_fallback_order_explicit(self):
        """Explicit fallback_order parsed correctly."""
        cfg = _make_llm_config(
            provider="openai",
            fallback_order=["openai", "anthropic", "astron"],
        )
        assert cfg.fallback_order == ["openai", "anthropic", "astron"]


class TestFallbackBehavior:
    def test_503_falls_back_to_anthropic(self):
        """openai returns 503 → tries anthropic → success."""
        cfg = _make_llm_config(
            provider="openai",
            fallback_order=["openai", "anthropic"],
        )
        client = SignalLLMClient(cfg)

        openai_client = _make_mock_client("from-openai")
        openai_client.chat.completions.create.side_effect = _make_error_with_status(503)
        anthropic_client = MagicMock()
        # Anthropic uses .messages.create, return a content block.
        content_block = MagicMock()
        content_block.text = "from-anthropic"
        response = MagicMock()
        response.content = [content_block]
        response.usage = None
        anthropic_client.messages.create.return_value = response

        client._clients = {"openai": openai_client, "anthropic": anthropic_client}

        result = client._call_llm("hello", json_mode=False)
        assert result == "from-anthropic"
        assert openai_client.chat.completions.create.called
        assert anthropic_client.messages.create.called

    def test_503_falls_back_to_astron(self):
        """anthropic also 503 → tries astron → success (3-provider chain)."""
        cfg = _make_llm_config(
            provider="openai",
            fallback_order=["openai", "anthropic", "astron"],
        )
        client = SignalLLMClient(cfg)

        openai_client = _make_mock_client("from-openai")
        openai_client.chat.completions.create.side_effect = _make_error_with_status(503)
        anthropic_client = MagicMock()
        anthropic_client.messages.create.side_effect = _make_error_with_status(503)
        astron_client = _make_mock_client("from-astron")

        client._clients = {
            "openai": openai_client,
            "anthropic": anthropic_client,
            "astron": astron_client,
        }

        result = client._call_llm("hello", json_mode=False)
        assert result == "from-astron"
        assert openai_client.chat.completions.create.called
        assert anthropic_client.messages.create.called
        assert astron_client.chat.completions.create.called

    def test_all_providers_exhausted_raises(self):
        """All providers fail with 503 → raises last error."""
        cfg = _make_llm_config(
            provider="openai",
            fallback_order=["openai", "anthropic"],
        )
        client = SignalLLMClient(cfg)

        openai_client = MagicMock()
        openai_client.chat.completions.create.side_effect = _make_error_with_status(503, "openai-503")
        anthropic_client = MagicMock()
        anthropic_client.messages.create.side_effect = _make_error_with_status(503, "anthropic-503")

        client._clients = {"openai": openai_client, "anthropic": anthropic_client}

        with pytest.raises(Exception) as exc_info:
            client._call_llm("hello", json_mode=False)
        assert "anthropic-503" in str(exc_info.value)

    def test_timeout_is_transient(self):
        """TimeoutError triggers fallback."""
        cfg = _make_llm_config(
            provider="openai",
            fallback_order=["openai", "anthropic"],
        )
        client = SignalLLMClient(cfg)

        openai_client = MagicMock()
        openai_client.chat.completions.create.side_effect = TimeoutError("read timed out")
        anthropic_client = MagicMock()
        content_block = MagicMock()
        content_block.text = "from-anthropic"
        response = MagicMock()
        response.content = [content_block]
        response.usage = None
        anthropic_client.messages.create.return_value = response

        client._clients = {"openai": openai_client, "anthropic": anthropic_client}

        result = client._call_llm("hello", json_mode=False)
        assert result == "from-anthropic"

    def test_connection_error_is_transient(self):
        """ConnectionError triggers fallback."""
        cfg = _make_llm_config(
            provider="openai",
            fallback_order=["openai", "anthropic"],
        )
        client = SignalLLMClient(cfg)

        openai_client = MagicMock()
        openai_client.chat.completions.create.side_effect = ConnectionError("conn refused")
        anthropic_client = MagicMock()
        content_block = MagicMock()
        content_block.text = "from-anthropic"
        response = MagicMock()
        response.content = [content_block]
        response.usage = None
        anthropic_client.messages.create.return_value = response

        client._clients = {"openai": openai_client, "anthropic": anthropic_client}

        result = client._call_llm("hello", json_mode=False)
        assert result == "from-anthropic"

    def test_bad_request_does_not_fallback(self):
        """400 BadRequest raises immediately, no fallback attempted."""
        cfg = _make_llm_config(
            provider="openai",
            fallback_order=["openai", "anthropic"],
        )
        client = SignalLLMClient(cfg)

        openai_client = MagicMock()
        openai_client.chat.completions.create.side_effect = _make_error_with_status(400, "bad request")
        anthropic_client = MagicMock()

        client._clients = {"openai": openai_client, "anthropic": anthropic_client}

        with pytest.raises(Exception) as exc_info:
            client._call_llm("hello", json_mode=False)
        assert "bad request" in str(exc_info.value)
        # Anthropic must NOT have been called — 400 is permanent.
        assert not anthropic_client.messages.create.called

    def test_dedupe_dead_provider_skip(self):
        """Provider missing from the pool (failed construction) is skipped."""
        cfg = _make_llm_config(
            provider="openai",
            fallback_order=["openai", "anthropic"],
        )
        client = SignalLLMClient(cfg)

        # Simulate anthropic being a dead provider (not in pool).
        openai_client = _make_mock_client("from-openai")
        client._clients = {"openai": openai_client}  # anthropic missing

        result = client._call_llm("hello", json_mode=False)
        assert result == "from-openai"
        # Only openai was called; no KeyError on missing anthropic.

    def test_non_transient_error_reraised_not_swallowed(self):
        """Non-transient error (ValueError with no status_code) raised, not swallowed."""
        cfg = _make_llm_config(
            provider="openai",
            fallback_order=["openai", "anthropic"],
        )
        client = SignalLLMClient(cfg)

        openai_client = MagicMock()
        # ValueError with no status_code attribute → _is_transient_error returns False.
        openai_client.chat.completions.create.side_effect = ValueError("schema mismatch")
        anthropic_client = MagicMock()

        client._clients = {"openai": openai_client, "anthropic": anthropic_client}

        with pytest.raises(ValueError) as exc_info:
            client._call_llm("hello", json_mode=False)
        assert "schema mismatch" in str(exc_info.value)
        # Permanent error: anthropic NOT called.
        assert not anthropic_client.messages.create.called


class TestConfigValidation:
    def test_unknown_provider_in_fallback_raises(self):
        """fallback_order contains unknown provider → ValueError at config load."""
        with pytest.raises(ValueError) as exc_info:
            _make_llm_config(
                provider="openai",
                fallback_order=["openai", "nonexistent_provider"],
            )
        assert "nonexistent_provider" in str(exc_info.value)

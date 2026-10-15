from __future__ import annotations

import io
import json
import urllib.error

from ai_market_pulse.config import LLMSettings
from ai_market_pulse.llm import (
    _chat_completion,
    _read_cache,
    _render_prompt_template,
    _write_cache,
)


def test_llm_cache_roundtrip(tmp_path) -> None:
    settings = LLMSettings(cache_enabled=True, cache_dir=str(tmp_path))
    payload = {"model": "test", "messages": [{"role": "user", "content": "hello"}]}

    assert _read_cache(payload, settings) is None
    _write_cache(payload, settings, "cached")

    assert _read_cache(payload, settings) == "cached"


def test_prompt_template_renders_context(tmp_path) -> None:
    prompt = tmp_path / "asset.md"
    prompt.write_text("Context:\n{context}", encoding="utf-8")
    settings = LLMSettings(prompts_dir=str(tmp_path))

    rendered = _render_prompt_template(settings, "asset.md", "fallback", {"context": {"a": 1}})

    assert '"a": 1' in rendered


def test_prompt_template_ignores_stray_braces(tmp_path) -> None:
    prompt = tmp_path / "asset.md"
    prompt.write_text(
        'Context:\n{context}\nExample payload: {"example": 1}',
        encoding="utf-8",
    )
    settings = LLMSettings(prompts_dir=str(tmp_path))

    rendered = _render_prompt_template(settings, "asset.md", "fallback", {"context": {"a": 1}})

    assert isinstance(rendered, str)
    # The stray literal brace shouldn't take down substitution of the
    # well-formed {context} placeholder elsewhere in the same template.
    assert '"a": 1' in rendered
    assert '{"example": 1}' in rendered


def test_prompt_template_ignores_stray_braces_without_colon_space(tmp_path) -> None:
    prompt = tmp_path / "asset.md"
    prompt.write_text("Context:\n{context}\nSee {unknown_key} for details.", encoding="utf-8")
    settings = LLMSettings(prompts_dir=str(tmp_path))

    rendered = _render_prompt_template(settings, "asset.md", "fallback", {"context": {"a": 1}})

    assert '"a": 1' in rendered
    assert "{unknown_key}" in rendered


def _raise_if_called(*args, **kwargs):
    raise AssertionError("urlopen should not be called")


def test_chat_completion_disabled_skips_network(monkeypatch) -> None:
    settings = LLMSettings(enabled=False, model="gpt-4", cache_enabled=False)
    monkeypatch.setattr("ai_market_pulse.llm.urllib.request.urlopen", _raise_if_called)

    result = _chat_completion([{"role": "user", "content": "hi"}], settings)

    assert result is None


def test_chat_completion_missing_api_key_skips_network(monkeypatch) -> None:
    settings = LLMSettings(
        enabled=True,
        model="gpt-4",
        api_key_env="AI_MARKET_PULSE_TEST_MISSING_KEY",
        cache_enabled=False,
    )
    monkeypatch.delenv("AI_MARKET_PULSE_TEST_MISSING_KEY", raising=False)
    monkeypatch.setattr("ai_market_pulse.llm.urllib.request.urlopen", _raise_if_called)

    result = _chat_completion([{"role": "user", "content": "hi"}], settings)

    assert result is None


def test_chat_completion_missing_model_skips_network(monkeypatch) -> None:
    settings = LLMSettings(
        enabled=True,
        model=None,
        api_key_env="AI_MARKET_PULSE_TEST_KEY",
        cache_enabled=False,
    )
    monkeypatch.setenv("AI_MARKET_PULSE_TEST_KEY", "secret")
    monkeypatch.setattr("ai_market_pulse.llm.urllib.request.urlopen", _raise_if_called)

    result = _chat_completion([{"role": "user", "content": "hi"}], settings)

    assert result is None


def test_chat_completion_cache_hit_skips_network(tmp_path, monkeypatch) -> None:
    settings = LLMSettings(
        enabled=True,
        model="gpt-4",
        api_key_env="AI_MARKET_PULSE_TEST_KEY",
        cache_enabled=True,
        cache_dir=str(tmp_path),
    )
    monkeypatch.setenv("AI_MARKET_PULSE_TEST_KEY", "secret")
    messages = [{"role": "user", "content": "hi"}]
    payload = {"model": settings.model, "temperature": settings.temperature, "messages": messages}
    _write_cache(payload, settings, "cached content")
    monkeypatch.setattr("ai_market_pulse.llm.urllib.request.urlopen", _raise_if_called)

    result = _chat_completion(messages, settings)

    assert result == "cached content"


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_chat_completion_success_parses_and_caches(tmp_path, monkeypatch) -> None:
    settings = LLMSettings(
        enabled=True,
        model="gpt-4",
        api_key_env="AI_MARKET_PULSE_TEST_KEY",
        cache_enabled=True,
        cache_dir=str(tmp_path),
    )
    monkeypatch.setenv("AI_MARKET_PULSE_TEST_KEY", "secret")
    response_body = json.dumps(
        {"choices": [{"message": {"content": " Hello there "}}]}
    ).encode("utf-8")

    def fake_urlopen(request, timeout=None):
        return _FakeResponse(response_body)

    monkeypatch.setattr("ai_market_pulse.llm.urllib.request.urlopen", fake_urlopen)

    messages = [{"role": "user", "content": "hi"}]
    result = _chat_completion(messages, settings)

    assert result == "Hello there"
    payload = {"model": settings.model, "temperature": settings.temperature, "messages": messages}
    assert _read_cache(payload, settings) == "Hello there"


def test_chat_completion_network_exception_returns_none(tmp_path, monkeypatch) -> None:
    settings = LLMSettings(
        enabled=True,
        model="gpt-4",
        api_key_env="AI_MARKET_PULSE_TEST_KEY",
        cache_enabled=False,
    )
    monkeypatch.setenv("AI_MARKET_PULSE_TEST_KEY", "secret")

    def fake_urlopen(request, timeout=None):
        raise OSError("boom")

    monkeypatch.setattr("ai_market_pulse.llm.urllib.request.urlopen", fake_urlopen)

    result = _chat_completion([{"role": "user", "content": "hi"}], settings)

    assert result is None


def test_chat_completion_malformed_json_returns_none(tmp_path, monkeypatch) -> None:
    settings = LLMSettings(
        enabled=True,
        model="gpt-4",
        api_key_env="AI_MARKET_PULSE_TEST_KEY",
        cache_enabled=False,
    )
    monkeypatch.setenv("AI_MARKET_PULSE_TEST_KEY", "secret")

    def fake_urlopen(request, timeout=None):
        return _FakeResponse(b"not json")

    monkeypatch.setattr("ai_market_pulse.llm.urllib.request.urlopen", fake_urlopen)

    result = _chat_completion([{"role": "user", "content": "hi"}], settings)

    assert result is None


def test_llm_retries_on_network_error(tmp_path, monkeypatch) -> None:
    settings = LLMSettings(
        enabled=True,
        model="gpt-4",
        api_key_env="AI_MARKET_PULSE_TEST_KEY",
        cache_enabled=False,
    )
    monkeypatch.setenv("AI_MARKET_PULSE_TEST_KEY", "secret")

    response_body = json.dumps(
        {"choices": [{"message": {"content": "recovered"}}]}
    ).encode("utf-8")

    call_count = {"n": 0}

    def fake_urlopen(request, timeout=None):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise urllib.error.URLError("transient network error")
        return _FakeResponse(response_body)

    monkeypatch.setattr("ai_market_pulse.llm.urllib.request.urlopen", fake_urlopen)

    result = _chat_completion([{"role": "user", "content": "hi"}], settings)

    assert result == "recovered"
    assert call_count["n"] == 3


def test_llm_logs_warning_on_persistent_failure(tmp_path, monkeypatch, caplog) -> None:
    settings = LLMSettings(
        enabled=True,
        model="gpt-4",
        api_key_env="AI_MARKET_PULSE_TEST_KEY",
        cache_enabled=False,
    )
    monkeypatch.setenv("AI_MARKET_PULSE_TEST_KEY", "secret")

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("persistent network error")

    monkeypatch.setattr("ai_market_pulse.llm.urllib.request.urlopen", fake_urlopen)

    with caplog.at_level("WARNING", logger="ai_market_pulse.llm"):
        result = _chat_completion([{"role": "user", "content": "hi"}], settings)

    assert result is None
    assert any(
        "LLM chat completion failed after retries" in record.message
        and record.levelname == "WARNING"
        for record in caplog.records
    )


def test_llm_does_not_retry_on_http_4xx(tmp_path, monkeypatch) -> None:
    settings = LLMSettings(
        enabled=True,
        model="gpt-4",
        api_key_env="AI_MARKET_PULSE_TEST_KEY",
        cache_enabled=False,
    )
    monkeypatch.setenv("AI_MARKET_PULSE_TEST_KEY", "secret")

    call_count = {"n": 0}

    def fake_urlopen(request, timeout=None):
        call_count["n"] += 1
        # 401 Unauthorized — client error, must not be retried.
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            {},
            io.BytesIO(b""),
        )

    monkeypatch.setattr("ai_market_pulse.llm.urllib.request.urlopen", fake_urlopen)

    result = _chat_completion([{"role": "user", "content": "hi"}], settings)

    assert result is None
    assert call_count["n"] == 1

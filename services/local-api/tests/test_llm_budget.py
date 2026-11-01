"""Daily LLM token ledger, the settings ceiling, and the gate that honours it."""

from __future__ import annotations

import json

import pytest
from app.core.llm_usage import (
    LLMUsageLedger,
    budget_exceeded,
    budget_state,
    reset_ledger_for_tests,
)
from app.core.settings_service import SettingsService
from app.main import create_app
from httpx import ASGITransport, AsyncClient

SETTINGS_URL = "/api/v1/settings"
USAGE_URL = "/api/v1/settings/llm-usage"


@pytest.fixture
def ledger(tmp_path):
    """Point the process ledger at tmp so tests never touch data/llm_usage.json."""
    return reset_ledger_for_tests(tmp_path / "llm_usage.json")


def _service(tmp_path) -> SettingsService:
    return SettingsService(config_path=tmp_path / "settings.json")


def _wire_app(tmp_path, service=None):
    from app.core.dependencies import get_settings_service

    service = service or _service(tmp_path)
    app = create_app()
    app.dependency_overrides[get_settings_service] = lambda: service
    return app, service


async def _call(app, method: str, path: str, **kw):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await getattr(c, method)(path, **kw)


def _budget_payload(**fields) -> dict:
    return {"llm_budget": fields}


# ── ledger ────────────────────────────────────────────────────────────────


def test_ledger_accumulates_per_source(ledger):
    ledger.record(
        source="helper", prompt_tokens=100, completion_tokens=50, total_tokens=150
    )
    ledger.record(
        source="pa_analysis", prompt_tokens=10, completion_tokens=5, total_tokens=15
    )
    snap = ledger.snapshot()
    assert snap["total_tokens"] == 165
    assert snap["calls"] == 2
    assert snap["by_source"]["helper"] == {"calls": 1, "total_tokens": 150}
    assert snap["by_source"]["pa_analysis"] == {"calls": 1, "total_tokens": 15}


def test_ledger_persists_across_restart(tmp_path, ledger):
    """The ceiling a restart silently lifts is not a ceiling."""
    ledger.record(
        source="helper", prompt_tokens=7, completion_tokens=3, total_tokens=10
    )
    reopened = LLMUsageLedger(tmp_path / "llm_usage.json")
    assert reopened.snapshot()["total_tokens"] == 10


def test_ledger_drops_a_previous_day(tmp_path):
    """Yesterday's spend must not follow the user into today."""
    path = tmp_path / "llm_usage.json"
    path.write_text(
        json.dumps(
            {
                "day": "1999-01-01",
                "prompt_tokens": 999,
                "completion_tokens": 999,
                "total_tokens": 1998,
                "calls": 5,
                "by_source": {},
            }
        ),
        encoding="utf-8",
    )
    fresh = LLMUsageLedger(path)
    assert fresh.snapshot()["total_tokens"] == 0


def test_ledger_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "llm_usage.json"
    path.write_text("{not json", encoding="utf-8")
    fresh = LLMUsageLedger(path)
    assert fresh.snapshot()["total_tokens"] == 0
    fresh.record(source="helper", total_tokens=4)
    assert LLMUsageLedger(path).snapshot()["total_tokens"] == 4


def test_ledger_total_only_usage_still_counts(tmp_path):
    """A provider that reports only a total must still move the day total."""
    fresh = LLMUsageLedger(tmp_path / "llm_usage.json")
    fresh.record(source="helper", total_tokens=42)
    snap = fresh.snapshot()
    assert snap["total_tokens"] == 42
    assert snap["prompt_tokens"] == 0


# ── ceiling semantics ─────────────────────────────────────────────────────


def test_default_is_no_cap(tmp_path, ledger):
    """0 = no cap is this machine's default; it must never block anything."""
    service = _service(tmp_path)
    assert service.get_llm_budget() == {"daily_tokens": 0}
    ledger.record(source="helper", total_tokens=10_000_000)
    assert budget_exceeded(service) is False


def test_no_cap_renders_remaining_as_none(tmp_path, ledger):
    service = _service(tmp_path)
    ledger.record(source="helper", total_tokens=500)
    state = budget_state(service)
    assert state["remaining"] is None
    assert state["exceeded"] is False
    assert state["daily_tokens"] == 0


def test_cap_is_reached_at_the_threshold_not_past_it(tmp_path, ledger):
    import asyncio

    service = _service(tmp_path)
    asyncio.run(service.update_settings({"llm_budget": {"daily_tokens": 100}}))
    ledger.record(source="helper", total_tokens=99)
    assert budget_exceeded(service) is False
    ledger.record(source="helper", total_tokens=1)
    assert budget_exceeded(service) is True
    assert budget_state(service)["remaining"] == 0


def test_budget_setting_round_trips_and_survives_restart(tmp_path):
    import asyncio

    service = _service(tmp_path)
    asyncio.run(service.update_settings({"llm_budget": {"daily_tokens": 250_000}}))
    restarted = _service(tmp_path)
    assert restarted.get_llm_budget() == {"daily_tokens": 250_000}


def test_negative_budget_clamps_to_no_cap(tmp_path):
    import asyncio

    service = _service(tmp_path)
    asyncio.run(service.update_settings({"llm_budget": {"daily_tokens": -5}}))
    assert _service(tmp_path).get_llm_budget()["daily_tokens"] == 0


def test_non_numeric_budget_is_rejected_not_guessed(tmp_path):
    import asyncio

    service = _service(tmp_path)
    asyncio.run(service.update_settings({"llm_budget": {"daily_tokens": "lots"}}))
    assert _service(tmp_path).get_llm_budget()["daily_tokens"] == 0


def test_unreadable_settings_treats_budget_as_no_cap(ledger):
    """A read failure must not invent a ceiling and stop the user's automation."""

    class _Broken:
        def get_llm_budget(self):
            raise RuntimeError("disk gone")

    ledger.record(source="helper", total_tokens=10**9)
    assert budget_exceeded(_Broken()) is False


# ── API surface ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_usage_endpoint_reports_today_and_ceiling(tmp_path, ledger):
    app, service = _wire_app(tmp_path)

    await service.update_settings({"llm_budget": {"daily_tokens": 1000}})
    ledger.record(
        source="pa_analysis", prompt_tokens=600, completion_tokens=100, total_tokens=700
    )
    resp = await _call(app, "get", USAGE_URL)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tokens_today"] == 700
    assert data["daily_tokens"] == 1000
    assert data["exceeded"] is False
    assert data["remaining"] == 300
    assert data["by_source"]["pa_analysis"]["total_tokens"] == 700


@pytest.mark.asyncio
async def test_usage_endpoint_flags_the_pause(tmp_path, ledger):
    app, service = _wire_app(tmp_path)
    await service.update_settings({"llm_budget": {"daily_tokens": 100}})
    ledger.record(source="helper", total_tokens=100)
    data = (await _call(app, "get", USAGE_URL)).json()["data"]
    assert data["exceeded"] is True
    assert data["remaining"] == 0


@pytest.mark.asyncio
async def test_budget_section_is_visible_on_the_settings_page(tmp_path):
    app, _ = _wire_app(tmp_path)
    data = (await _call(app, "get", SETTINGS_URL)).json()["data"]
    assert data["llm_budget"] == {"daily_tokens": 0}


@pytest.mark.asyncio
async def test_budget_put_round_trips_through_the_api(tmp_path):
    app, _ = _wire_app(tmp_path)
    resp = await _call(
        app, "put", SETTINGS_URL, json=_budget_payload(daily_tokens=12345)
    )
    assert resp.status_code == 200
    restarted = _service(tmp_path)
    assert restarted.get_llm_budget() == {"daily_tokens": 12345}


# ── call_llm gate ─────────────────────────────────────────────────────────


class _FakeProvider:
    """Stands in for OpenAICompatProvider: real usage, no network."""

    calls = 0

    def __init__(self, *_a, **_kw):
        pass

    async def chat_with_usage(self, *a, **kw):
        from selene_space_llm.schemas import TokenUsage

        type(self).calls += 1
        return "ok", TokenUsage(prompt_tokens=30, completion_tokens=20, total_tokens=50)


class _StubSettings:
    def __init__(self, daily_tokens: int = 0) -> None:
        self.daily_tokens = daily_tokens

    async def resolve_llm_full_config(self):
        return {"api_key": "k", "model": "m", "base_url": ""}

    def get_llm_budget(self):
        return {"daily_tokens": self.daily_tokens}


def _patch_provider(monkeypatch):
    import selene_space_llm.openai_compat as oc

    _FakeProvider.calls = 0
    monkeypatch.setattr(oc, "OpenAICompatProvider", _FakeProvider)


@pytest.mark.asyncio
async def test_call_llm_records_real_usage(ledger, monkeypatch):
    from app.core import llm_helper

    _patch_provider(monkeypatch)
    text = await llm_helper.call_llm(_StubSettings(), "hi", source="helper")
    assert text == "ok"
    snap = ledger.snapshot()
    assert snap["total_tokens"] == 50
    assert snap["by_source"]["helper"]["calls"] == 1


@pytest.mark.asyncio
async def test_call_llm_unaffected_by_default_no_cap(ledger, monkeypatch):
    """0 = no cap: the default must leave every call exactly as before."""
    from app.core import llm_helper

    _patch_provider(monkeypatch)
    for _ in range(3):
        assert await llm_helper.call_llm(_StubSettings(0), "hi") == "ok"
    assert _FakeProvider.calls == 3


@pytest.mark.asyncio
async def test_call_llm_pauses_once_the_cap_is_reached(ledger, monkeypatch):
    from app.core import llm_helper

    _patch_provider(monkeypatch)
    settings = _StubSettings(daily_tokens=50)
    assert await llm_helper.call_llm(settings, "hi") == "ok"
    # The first call spent exactly the ceiling; the next must not reach the provider.
    assert await llm_helper.call_llm(settings, "hi") is None
    assert _FakeProvider.calls == 1


@pytest.mark.asyncio
async def test_call_llm_pause_does_not_record_a_phantom_call(ledger, monkeypatch):
    from app.core import llm_helper

    _patch_provider(monkeypatch)
    settings = _StubSettings(daily_tokens=50)
    await llm_helper.call_llm(settings, "hi")
    before = ledger.snapshot()
    await llm_helper.call_llm(settings, "hi")
    assert ledger.snapshot() == before


# ── truncation is not "the model said this" ───────────────────────────────


class _TruncatingProvider:
    """First call reports finish_reason=length, the uncapped retry succeeds.

    This is the production shape (2026-10-25): a reasoning model spends the
    output budget on thinking, HTTP still returns 200 with a half-written JSON
    body, and every caller that only checks for emptiness treats the fragment
    as a complete reply.
    """

    calls: list[int | None] = []

    def __init__(self, *_a, **_kw):
        pass

    async def chat_with_usage(self, *a, **kw):
        from selene_space_llm.schemas import TokenUsage

        max_tokens = kw.get("max_tokens")
        type(self).calls.append(max_tokens)
        if max_tokens is not None:
            return '{"signal":"untested","fact":"half', TokenUsage(
                prompt_tokens=10,
                completion_tokens=max_tokens,
                total_tokens=10 + max_tokens,
                finish_reason="length",
            )
        return '{"signal":"untested","fact":"full"}', TokenUsage(
            prompt_tokens=10,
            completion_tokens=40,
            total_tokens=50,
            finish_reason="stop",
        )


@pytest.mark.asyncio
async def test_call_llm_retries_when_the_reply_was_truncated(ledger, monkeypatch):
    """A half-written body must never be returned as the model's answer."""
    from app.core import llm_helper

    _TruncatingProvider.calls = []
    monkeypatch.setattr(
        "selene_space_llm.openai_compat.OpenAICompatProvider", _TruncatingProvider
    )
    text = await llm_helper.call_llm(_StubSettings(), "hi", max_tokens=700)
    assert text == '{"signal":"untested","fact":"full"}'
    # First attempt capped, retry uncapped — same policy as the empty-response path.
    assert _TruncatingProvider.calls == [700, None]


@pytest.mark.asyncio
async def test_call_llm_does_not_retry_a_complete_reply(ledger, monkeypatch):
    """finish_reason="stop" must stay a single call (no silent double spend)."""
    from app.core import llm_helper

    class _WholeProvider:
        calls = 0

        def __init__(self, *_a, **_kw):
            pass

        async def chat_with_usage(self, *a, **kw):
            from selene_space_llm.schemas import TokenUsage

            type(self).calls += 1
            return "whole", TokenUsage(total_tokens=5, finish_reason="stop")

    monkeypatch.setattr(
        "selene_space_llm.openai_compat.OpenAICompatProvider", _WholeProvider
    )
    assert await llm_helper.call_llm(_StubSettings(), "hi", max_tokens=700) == "whole"
    assert _WholeProvider.calls == 1

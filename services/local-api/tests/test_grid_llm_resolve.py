"""Tests for grid sentinel LLM resolution and the auto-patrol gate.

Covers:
  - ``build_provider`` / ``_resolve_llm_provider`` priority chain
    (SELENE_LLM_* env -> AI_* env -> keyring fallback -> None)
  - ``SELENE_GRID_SENTINEL_AUTO`` gate on ``POST /grid/start``
  - ``SentinelScheduler.status()`` exposing ``running``

Isolation notes (see internal-plan): the shared
``tests/grid`` fixture only saves/restores ``_engines``/``_adapters``, not
the ``_scheduler`` singleton, so leaking a scheduler here would make
``test_grid_api.py::TestSentinel::test_sentinel_no_scheduler`` flaky.
Every test below that touches ``_scheduler`` uses the ``clean_scheduler``
fixture, which restores the global afterwards.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.api.v1.grid as grid_mod


@pytest.fixture
def client():
    """TestClient over the grid router (mirrors tests/grid/test_grid_api.py).

    Defined locally because that module's fixture lives in a different
    test directory and is not visible here.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(grid_mod.router, prefix="/api/v1")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def clean_scheduler():
    """Save/restore the module-level ``_scheduler`` singleton.

    ``SentinelScheduler`` is patched to a MagicMock in the gate tests, so
    no real background task is ever created; restoring the global is enough.
    """
    saved = grid_mod._scheduler
    grid_mod._scheduler = None
    yield
    grid_mod._scheduler = saved


class _Provider:
    """Minimal stand-in matching ``OpenAICompatProvider.config.api_key``."""

    def __init__(self, api_key: str | None) -> None:
        self.config = type("Cfg", (), {"api_key": api_key})()


# ---------------------------------------------------------------------------
# AC2 / AC3 / AC4 — resolution priority chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_empty_falls_back_to_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env key -> provider comes from the keyring chain (AC2)."""
    for var in ("SELENE_LLM_API_KEY", "AI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    sentinel_provider = _Provider("keyring-key")

    async def _fake_build(settings_service: Any) -> Any:
        return sentinel_provider

    with patch("app.core.llm_helper.build_provider", _fake_build):
        resolved = await grid_mod._resolve_llm_provider()

    assert resolved is sentinel_provider


@pytest.mark.asyncio
async def test_env_key_wins_over_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env key present -> keyring chain is never consulted (AC3)."""
    monkeypatch.setenv("SELENE_LLM_API_KEY", "env-key")
    monkeypatch.setenv("SELENE_LLM_BASE_URL", "https://env.example.com/v1")
    monkeypatch.delenv("AI_API_KEY", raising=False)

    keyring_build = MagicMock(side_effect=AssertionError("keyring must not be used"))
    with patch("app.core.llm_helper.build_provider", keyring_build):
        resolved = await grid_mod._resolve_llm_provider()

    keyring_build.assert_not_called()
    assert resolved is not None
    assert grid_mod._provider_has_key(resolved)


@pytest.mark.asyncio
async def test_both_empty_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither env nor keyring configured -> None, no exception (AC4)."""
    for var in ("SELENE_LLM_API_KEY", "AI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    async def _none(settings_service: Any) -> Any:
        return None

    with patch("app.core.llm_helper.build_provider", _none):
        resolved = await grid_mod._resolve_llm_provider()

    assert resolved is None


@pytest.mark.asyncio
async def test_build_provider_none_key_returns_none() -> None:
    """build_provider returns None when the keyring has no api_key."""

    class _Svc:
        async def resolve_llm_full_config(self, **_kw: Any) -> dict[str, Any]:
            return {"api_key": "", "base_url": "", "model": ""}

    from app.core.llm_helper import build_provider

    assert await build_provider(_Svc()) is None


@pytest.mark.asyncio
async def test_build_provider_normalizes_base_url() -> None:
    """build_provider appends /v1 and keeps the sentinel token budget."""

    class _Svc:
        async def resolve_llm_full_config(self, **_kw: Any) -> dict[str, Any]:
            return {"api_key": "k", "base_url": "http://127.0.0.1:7863", "model": "glm"}

    from app.core.llm_helper import build_provider

    provider = await build_provider(_Svc())
    assert provider is not None
    assert provider.config.base_url == "http://127.0.0.1:7863/v1"
    # max_tokens must stay 16384: grid tools call chat() bare with no retry,
    # and glm thinking models return empty content at the 4096 default.
    assert provider.config.max_tokens == 16384


# ---------------------------------------------------------------------------
# AC8 — SELENE_LLM_* wins over AI_* (gap F8 leaves uncovered upstream)
# ---------------------------------------------------------------------------


def test_selene_env_wins_over_ai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELENE_LLM_API_KEY", "selene-key")
    monkeypatch.setenv("SELENE_LLM_BASE_URL", "https://selene.example.com/v1")
    monkeypatch.setenv("SELENE_LLM_MODEL", "selene-model")
    monkeypatch.setenv("AI_API_KEY", "ai-key")
    monkeypatch.setenv("AI_BASE_URL", "https://ai.example.com/v1")
    monkeypatch.setenv("AI_MODEL", "ai-model")

    provider = grid_mod._create_llm_provider()
    assert provider is not None
    assert provider.config.api_key == "selene-key"
    assert provider.config.base_url == "https://selene.example.com/v1"
    assert provider.config.model == "selene-model"


# ---------------------------------------------------------------------------
# AC5 / AC6 — SELENE_GRID_SENTINEL_AUTO gate on POST /grid/start
# ---------------------------------------------------------------------------


def _patch_scheduler_and_engine(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch SentinelScheduler (source module) and register a mock engine.

    Returns the mock scheduler so callers can assert on ``start``.
    """
    mock_sched = MagicMock()
    mock_sched._running = False
    mock_sched.start = AsyncMock()
    mock_sched.stop = AsyncMock()

    monkeypatch.setattr("app.sentinel_scheduler.SentinelScheduler", lambda *a, **k: mock_sched)
    monkeypatch.setattr(grid_mod, "_scheduler", None)

    engine = MagicMock()
    state = MagicMock()
    state.model_dump.return_value = {"running": True}
    engine.start_grid = AsyncMock(return_value=state)
    monkeypatch.setitem(grid_mod._engines, "ex", engine)
    return mock_sched


def _start_grid(client) -> Any:
    return client.post(
        "/api/v1/grid/start",
        params={
            "exchange": "ex",
            "market_id": 1,
            "mode": "neutral",
            "lower": 100.0,
            "upper": 200.0,
            "grid_count": 5,
            "size_base": 10.0,
        },
    )


def test_gate_off_does_not_start_scheduler(
    monkeypatch: pytest.MonkeyPatch, clean_scheduler, client
) -> None:
    """Default (unset) -> start_grid does not start the background loops (AC5)."""
    monkeypatch.delenv("SELENE_GRID_SENTINEL_AUTO", raising=False)
    mock_sched = _patch_scheduler_and_engine(monkeypatch)

    async def _no_llm() -> Any:
        return None

    monkeypatch.setattr(grid_mod, "_resolve_llm_provider", _no_llm)

    res = _start_grid(client)
    assert res.status_code == 200
    mock_sched.start.assert_not_called()


def test_gate_on_starts_scheduler(
    monkeypatch: pytest.MonkeyPatch, clean_scheduler, client
) -> None:
    """SELENE_GRID_SENTINEL_AUTO=1 -> start_grid starts the loops (AC6)."""
    monkeypatch.setenv("SELENE_GRID_SENTINEL_AUTO", "1")
    mock_sched = _patch_scheduler_and_engine(monkeypatch)

    async def _no_llm() -> Any:
        return None

    monkeypatch.setattr(grid_mod, "_resolve_llm_provider", _no_llm)

    res = _start_grid(client)
    assert res.status_code == 200
    mock_sched.start.assert_awaited_once()


# ---------------------------------------------------------------------------
# AC7 — status() exposes running
# ---------------------------------------------------------------------------


def test_status_exposes_running() -> None:
    from app.sentinel_scheduler import SentinelConfig, SentinelScheduler

    sched = SentinelScheduler({}, None, SentinelConfig())
    status = sched.status()
    assert "running" in status
    assert status["running"] is False

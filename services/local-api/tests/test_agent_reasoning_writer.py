"""Tests for the agent_reasoning writer (agent turn persistence).

Contract: after a turn completes successfully, the user message and the
assistant reply land in the agent_reasoning table (v008) and are readable
through GET /agent/reasoning/recent. Write is best-effort — a DB failure
must not break the streaming turn.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.core.dependencies import set_db_manager
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def db_manager():
    runner = MigrationRunner(ALL_MIGRATIONS)
    db = AsyncSQLiteManager(":memory:", migration_runner=runner)
    await db.connect()
    set_db_manager(db)
    yield db
    set_db_manager(None)
    await db.close()


@pytest.mark.asyncio
async def test_persist_reasoning_writes_user_and_assistant_rows(db_manager):
    from app.api.v1.agent import persist_turn_reasoning

    await persist_turn_reasoning(
        session_id="sess-r1",
        user_id="local",
        user_message="现在能买吗",
        assistant_text="回调区间观察，突破前不动",
        chains=["tool:fetch_klines", "tool:analyze"],
    )

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agent/reasoning/recent?session_id=sess-r1")

    assert resp.status_code == 200
    rows = resp.json()["data"]
    roles = {r["role"]: r for r in rows}
    assert set(roles) == {"user", "assistant"}
    assert roles["user"]["content"] == "现在能买吗"
    assert "回调区间观察" in roles["assistant"]["content"]
    assert "fetch_klines" in roles["assistant"]["chains_json"]


@pytest.mark.asyncio
async def test_persist_never_raises_when_db_broken():
    from app.api.v1.agent import persist_turn_reasoning

    class _BrokenDB:
        async def execute_many(self, sql: str, param_list: list) -> None:
            raise RuntimeError("db exploded")

    set_db_manager(_BrokenDB())
    try:
        await persist_turn_reasoning(
            session_id="s", user_id="local", user_message="m", assistant_text="a"
        )
    finally:
        set_db_manager(None)


@pytest.mark.asyncio
async def test_persist_noop_without_db():
    from app.api.v1.agent import persist_turn_reasoning

    set_db_manager(None)
    try:
        await persist_turn_reasoning(
            session_id="s", user_id="local", user_message="m", assistant_text="a"
        )
    finally:
        set_db_manager(None)


# ---------------------------------------------------------------------------
# N5: run settings recorded so a past turn can be reproduced later.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_settings_recorded_in_assistant_meta(db_manager):
    """A completed turn must record which model/settings produced it — two weeks
    later nobody can tell which model wrote a report otherwise."""
    import json

    from app.api.v1.agent import persist_turn_reasoning

    await persist_turn_reasoning(
        session_id="sess-meta",
        user_id="local",
        user_message="m",
        assistant_text="a",
        run_settings={"model": "glm-4.5-air", "temperature": 0.7},
    )

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agent/reasoning/recent?session_id=sess-meta")

    rows = resp.json()["data"]
    assistant = next(r for r in rows if r["role"] == "assistant")
    meta = json.loads(assistant["meta_json"])
    assert meta["model"] == "glm-4.5-air"


@pytest.mark.asyncio
async def test_run_settings_excludes_secrets(db_manager):
    """Whitelist discipline: endpoint and key must never be recorded, even if a
    caller passes them (a base_url can carry credentials)."""
    import json

    from app.api.v1.agent import persist_turn_reasoning

    await persist_turn_reasoning(
        session_id="sess-secret",
        user_id="local",
        user_message="m",
        assistant_text="a",
        run_settings={
            "model": "glm-4.5-air",
            "temperature": 0.7,
            "api_key": "sk-SECRET",
            "base_url": "https://user:pass@proxy.example/v1",
        },
    )

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agent/reasoning/recent?session_id=sess-secret")

    assistant = next(r for r in resp.json()["data"] if r["role"] == "assistant")
    blob = assistant["meta_json"]
    assert "SECRET" not in blob
    assert "pass@" not in blob
    # 白名单里的仍在
    assert json.loads(blob)["model"] == "glm-4.5-air"


@pytest.mark.asyncio
async def test_run_settings_absent_leaves_meta_null(db_manager):
    """No settings passed (e.g. an old caller) → meta_json stays NULL, not a
    fabricated empty object that would read as 'recorded: nothing'."""
    from app.api.v1.agent import persist_turn_reasoning

    await persist_turn_reasoning(
        session_id="sess-none", user_id="local", user_message="m", assistant_text="a"
    )
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agent/reasoning/recent?session_id=sess-none")
    assistant = next(r for r in resp.json()["data"] if r["role"] == "assistant")
    assert assistant["meta_json"] is None


def test_run_settings_from_runtime_reads_real_config_attr():
    """The provider stores its config on ``.config``; ``.config`` is where the
    model lives. (A ``hasattr(_llm, "_config")`` check is always False in this
    tree — that trap made the smart-model override dead code.)"""
    from app.api.v1.agent import _run_settings_from_runtime

    class _Config:
        model = "glm-4.5-air"
        temperature = 0.7
        max_tokens = 4096
        api_key = "sk-NEVER"
        base_url = "https://user:pass@proxy/v1"

    class _Provider:
        config = _Config()

    class _Runtime:
        _llm = _Provider()

    out = _run_settings_from_runtime(_Runtime())
    assert out == {"model": "glm-4.5-air", "temperature": 0.7, "max_tokens": 4096}
    assert "api_key" not in out and "base_url" not in out


def test_run_settings_from_runtime_degrades_when_config_absent():
    """A runtime whose provider exposes no config yields None, not a guess."""

    class _Bare:
        _llm = object()

    from app.api.v1.agent import _run_settings_from_runtime

    assert _run_settings_from_runtime(_Bare()) is None
    assert _run_settings_from_runtime(object()) is None

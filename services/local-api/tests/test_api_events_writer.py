"""Tests for P0-3: events_recent now has a writer, and its reader works.

The events pipeline (v008 table + /events/recent + EventsPanel) was dead in
two independent ways:
  1. no writer existed at all -> the table was always empty;
  2. the endpoint called ``db.query()``, but AsyncSQLiteManager only has
     ``execute()`` -> every read raised AttributeError and was swallowed
     into an empty list by the broad except.
Both are fixed. These tests lock the contract that AI tool actions land in
the stream and are readable through the real HTTP surface (calling the
handler directly bypasses FastAPI's Query() default injection, so we go
through the app).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.api.v1.events import record_event
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


async def _get(client: AsyncClient, path: str) -> list:
    resp = await client.get(path)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["data"], list)
    return body["data"]


@pytest.mark.asyncio
async def test_recorded_event_is_readable_via_http(db_manager):
    await record_event(
        kind="ai_action",
        title="AI 保存策略版本 v2",
        body="bb_v3 — 规则:entry=True",
        source="agent",
        payload={"strategy_id": "s1", "version": 2},
    )

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        rows = await _get(client, "/api/v1/events/recent")

    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "ai_action"
    assert row["title"] == "AI 保存策略版本 v2"
    assert row["source"] == "agent"


@pytest.mark.asyncio
async def test_kind_filter_matches(db_manager):
    await record_event(kind="ai_action", title="AI 动作", source="agent")
    await record_event(kind="system", title="系统事件", source="core")

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ai = await _get(client, "/api/v1/events/recent?kind=ai_action")
        sysrows = await _get(client, "/api/v1/events/recent?kind=system")

    assert [r["title"] for r in ai] == ["AI 动作"]
    assert [r["title"] for r in sysrows] == ["系统事件"]


@pytest.mark.asyncio
async def test_record_event_never_raises_when_db_broken(db_manager):
    """Observability writes must not break the action being observed."""

    class _BrokenDB:
        async def execute(self, sql: str, params: tuple = ()) -> list:
            raise RuntimeError("db exploded")

    set_db_manager(_BrokenDB())
    try:
        await record_event(kind="ai_action", title="x", source="agent")
    finally:
        set_db_manager(db_manager)


@pytest.mark.asyncio
async def test_record_event_noop_without_db(db_manager):
    """No db manager (service uninitialized) is a silent no-op, not a crash."""
    set_db_manager(None)
    try:
        await record_event(kind="ai_action", title="x", source="agent")
    finally:
        set_db_manager(db_manager)

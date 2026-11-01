"""Tests for Batch B: system_logs and agent_reasoning read paths work.

Both endpoints called ``db.query()`` — a method AsyncSQLiteManager does not
have — and the broad ``except`` swallowed the AttributeError into an empty
list, so the panels were permanently empty regardless of table contents.
Neither table has any writer in this tree (seeded here directly), but the
read path must still return what is in the table. Tests go through the real
HTTP surface because calling handlers directly bypasses FastAPI's Query()
default injection.
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
async def test_system_logs_seed_rows_readable_via_http(db_manager):
    await db_manager.execute(
        "INSERT INTO system_logs (id, timestamp, level, logger, message, module, line, exc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "log-1",
            1000,
            "error",
            "app.core",
            "Connection refused",
            "core.py",
            42,
            "Traceback...",
        ),
    )
    await db_manager.execute(
        "INSERT INTO system_logs (id, timestamp, level, logger, message, module, line, exc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("log-2", 2000, "info", "app.core", "Started worker", "core.py", 7, None),
    )

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/system/logs")

    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) == 2
    assert [r["id"] for r in rows] == ["log-2", "log-1"]
    assert rows[0]["level"] == "info"
    assert rows[0]["message"] == "Started worker"
    assert rows[1]["level"] == "error"


@pytest.mark.asyncio
async def test_system_logs_level_filter_matches(db_manager):
    await db_manager.execute(
        "INSERT INTO system_logs (id, timestamp, level, logger, message) "
        "VALUES (?, ?, ?, ?, ?)",
        ("log-e", 1000, "error", "app.core", "boom"),
    )
    await db_manager.execute(
        "INSERT INTO system_logs (id, timestamp, level, logger, message) "
        "VALUES (?, ?, ?, ?, ?)",
        ("log-i", 2000, "info", "app.core", "fine"),
    )

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/system/logs?level=error")

    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert [r["id"] for r in rows] == ["log-e"]


@pytest.mark.asyncio
async def test_agent_reasoning_seed_rows_readable_via_http(db_manager):
    await db_manager.execute(
        "INSERT INTO agent_reasoning (id, timestamp, session_id, user_id, symbol, role, content, chains_json, meta_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "r-1",
            1000,
            "sess-42",
            "local",
            "BTC/USDT",
            "assistant",
            "回调区间观察",
            "[]",
            "{}",
        ),
    )
    await db_manager.execute(
        "INSERT INTO agent_reasoning (id, timestamp, session_id, user_id, symbol, role, content, chains_json, meta_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("r-2", 2000, "sess-42", "local", "BTC/USDT", "user", "现在能买吗", None, None),
    )

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agent/reasoning/recent")

    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) == 2
    assert [r["id"] for r in rows] == ["r-2", "r-1"]
    assert rows[0]["role"] == "user"
    assert rows[0]["session_id"] == "sess-42"


@pytest.mark.asyncio
async def test_agent_reasoning_session_filter_matches(db_manager):
    await db_manager.execute(
        "INSERT INTO agent_reasoning (id, timestamp, session_id, user_id, symbol, role, content) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r-a", 1000, "sess-A", "local", "BTC/USDT", "assistant", "A 的回答"),
    )
    await db_manager.execute(
        "INSERT INTO agent_reasoning (id, timestamp, session_id, user_id, symbol, role, content) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r-b", 2000, "sess-B", "local", "ETH/USDT", "assistant", "B 的回答"),
    )

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agent/reasoning/recent?session_id=sess-A")

    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert [r["id"] for r in rows] == ["r-a"]

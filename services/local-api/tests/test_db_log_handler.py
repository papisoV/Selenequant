"""Tests for the DB log handler feeding the system_logs panel.

Contract:
  * INFO+ records emitted anywhere land in system_logs and are readable
    through GET /system/logs (the read path was fixed in the Batch B
    commit; this locks the writer side).
  * emit never raises even when formatting explodes.
  * drain never raises when the DB is broken, and drops the batch instead
    of wedging.
  * the handler ignores its own module's records (recursion guard).
"""

from __future__ import annotations

import logging

import pytest
import pytest_asyncio
from app.core.db_log_handler import DBLogHandler, attach_db_log_handler
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


def _make_handler() -> DBLogHandler:
    h = DBLogHandler()
    h.setFormatter(logging.Formatter("%(levelname)s:%(name)s: %(message)s"))
    return h


@pytest.mark.asyncio
async def test_emitted_record_drains_to_db_and_reads_back_via_http(db_manager):
    manager = attach_db_log_handler(lambda: db_manager)
    try:
        logging.getLogger("app.core.engines").error("启动引擎失败: K线源断连")
        assert manager.handler.pending == 1
        written = await manager.handler.drain(db_manager)
        assert written == 1
    finally:
        logging.getLogger().removeHandler(manager.handler)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/system/logs")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) == 1
    assert rows[0]["level"] == "error"
    assert "启动引擎失败" in rows[0]["message"]
    assert rows[0]["logger"] == "app.core.engines"


@pytest_asyncio.fixture
async def root_attached():
    """Attach a fresh handler to the root logger so emitted records reach it.

    Production sets root to INFO (main.py basicConfig); pytest's root is
    WARNING, which would swallow info records at the logger level before
    any handler sees them.
    """
    handler = _make_handler()
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    yield handler
    root.removeHandler(handler)
    root.setLevel(old_level)


def _record(level: int, name: str = "app.some.module") -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg="record %s",
        args=(logging.getLevelName(level),),
        exc_info=None,
        func="test",
    )


@pytest.mark.asyncio
async def test_info_enters_and_debug_does_not():
    """Level filter at the handler: INFO+ queued, DEBUG dropped.

    Constructed records instead of logger calls — pytest's capture plugin
    overrides child-logger levels, which would bypass the handler entirely
    and test its own plumbing rather than ours.
    """
    handler = _make_handler()
    handler.emit(_record(logging.DEBUG))
    assert handler.pending == 0
    handler.emit(_record(logging.INFO))
    handler.emit(_record(logging.WARNING))
    assert handler.pending == 2


@pytest.mark.asyncio
async def test_emit_never_raises_on_format_failure(root_attached):
    class _BadFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            raise RuntimeError("formatter exploded")

    bad = DBLogHandler()
    bad.setFormatter(_BadFormatter())
    logging.getLogger().addHandler(bad)
    try:
        logging.getLogger("app.x").info("this must not crash the pipeline")
    finally:
        logging.getLogger().removeHandler(bad)
    assert bad.pending == 0


@pytest.mark.asyncio
async def test_drain_never_raises_when_db_broken(root_attached):
    class _BrokenDB:
        async def execute_many(self, sql: str, param_list: list) -> None:
            raise RuntimeError("db exploded")

    logging.getLogger("app.y").warning("queued ok")
    assert root_attached.pending == 1
    written = await root_attached.drain(_BrokenDB())
    assert written == 0
    # batch was consumed — one bad write cannot wedge the queue
    assert root_attached.pending == 0


@pytest.mark.asyncio
async def test_recursion_guard_ignores_own_module(root_attached):
    logging.getLogger("app.core.db_log_handler").error("our own write failure")
    assert root_attached.pending == 0


@pytest.mark.asyncio
async def test_drain_noop_without_db(root_attached):
    logging.getLogger("app.z").info("queued")
    assert await root_attached.drain(None) == 0

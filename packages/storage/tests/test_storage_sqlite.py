"""AsyncSQLiteManager locks: connect/close lifecycle, context-manager exit,
execute + execute_many, connection-scoped transaction commit/rollback,
PRAGMA hardening, not-connected guard, and migration-runner wiring."""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import Migration, MigrationRunner
from selene_space_storage.sqlite import AsyncSQLiteManager, ConnectionError

MEM_DB = ":memory:"
SCRATCH_TABLE = "CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)"


@pytest_asyncio.fixture
async def db():
    manager = AsyncSQLiteManager(db_path=MEM_DB)
    await manager.connect()
    yield manager
    await manager.close()


def _select_val(where: str = "") -> str:
    return f"SELECT val FROM t{where}"


@pytest.mark.asyncio
async def test_is_connected_tracks_open_close():
    mgr = AsyncSQLiteManager(db_path=MEM_DB)
    assert mgr.is_connected is False
    await mgr.connect()
    assert mgr.is_connected is True
    await mgr.close()
    assert mgr.is_connected is False


@pytest.mark.asyncio
async def test_with_block_closes_on_exit():
    async with AsyncSQLiteManager(db_path=MEM_DB) as mgr:
        assert mgr.is_connected is True
    assert mgr.is_connected is False


@pytest.mark.asyncio
async def test_execute_inserts_and_selects_dicts(db: AsyncSQLiteManager):
    await db.execute(SCRATCH_TABLE)
    await db.execute("INSERT INTO t (val) VALUES (?)", ("greeting",))
    rows = await db.execute(_select_val())
    assert len(rows) == 1
    assert rows[0]["val"] == "greeting"


@pytest.mark.asyncio
async def test_execute_many_batches_parameter_sets(db: AsyncSQLiteManager):
    words = ["alpha", "beta", "gamma"]
    await db.execute(SCRATCH_TABLE)
    await db.execute_many(
        "INSERT INTO t (val) VALUES (?)",
        [(w,) for w in words],
    )
    rows = await db.execute(_select_val(" ORDER BY val"))
    assert [r["val"] for r in rows] == words


@pytest.mark.asyncio
async def test_connection_block_commits_writes(db: AsyncSQLiteManager):
    async with db.connection() as conn:
        await conn.execute(SCRATCH_TABLE)
        await conn.execute("INSERT INTO t (id) VALUES (7)")
    assert len(await db.execute("SELECT id FROM t")) == 1


@pytest.mark.asyncio
async def test_connection_block_rolls_back_on_error(db: AsyncSQLiteManager):
    await db.execute(SCRATCH_TABLE)
    await db.execute("INSERT INTO t (id, val) VALUES (7, 'keep-me')")

    with pytest.raises(RuntimeError, match="detonate"):
        async with db.connection() as conn:
            await conn.execute("UPDATE t SET val = 'drop-me' WHERE id = 7")
            raise RuntimeError("detonate")

    survivor = await db.execute(_select_val(" WHERE id = 7"))
    assert survivor[0]["val"] == "keep-me"


@pytest.mark.asyncio
async def test_journal_mode_pragma(db: AsyncSQLiteManager):
    # :memory: databases report "memory" for journal mode
    rows = await db.execute("PRAGMA journal_mode")
    assert rows[0][0] in ("wal", "memory")


@pytest.mark.asyncio
async def test_foreign_keys_pragma_is_on(db: AsyncSQLiteManager):
    rows = await db.execute("PRAGMA foreign_keys")
    assert rows[0][0] == 1


@pytest.mark.asyncio
async def test_query_before_connect_raises():
    cold = AsyncSQLiteManager(db_path=MEM_DB)
    with pytest.raises(ConnectionError, match="not connected"):
        await cold.execute("SELECT 40 + 2")


@pytest.mark.asyncio
async def test_default_runner_builds_core_tables():
    probe = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
    async with AsyncSQLiteManager(db_path=MEM_DB) as mgr:
        found = await mgr.execute(probe, ("strategies",))
    assert len(found) == 1


@pytest.mark.asyncio
async def test_injected_runner_replaces_default_set():
    async def _stamp(conn):
        await conn.execute("CREATE TABLE marker_only (id INTEGER PRIMARY KEY)")

    runner = MigrationRunner([Migration(id="v950", version=950, description="stamp", apply=_stamp)])
    probe = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
    async with AsyncSQLiteManager(db_path=MEM_DB, migration_runner=runner) as mgr:
        stamped = await mgr.execute(probe, ("marker_only",))
        core = await mgr.execute(probe, ("strategies",))
    assert len(stamped) == 1
    assert core == []

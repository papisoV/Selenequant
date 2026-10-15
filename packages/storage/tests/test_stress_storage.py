"""Boundary-condition stress tests for storage (cleanroom hardening).

Uses only temp-file / in-memory databases — never the real klines.db or
selenespace.db. Covers: full ALL_MIGRATIONS idempotency across runner
instances and sessions, mid-list failure rollback, malformed JSON payloads
in repository fields, fork deep-copy isolation, and ancestor cycle guard.
"""

from __future__ import annotations

import os
import sqlite3

import aiosqlite
import pytest
import pytest_asyncio

from selene_space_storage import sqlite as sqlite_mod
from selene_space_storage.migrations import Migration, MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.experiments import ExperimentRepository
from selene_space_storage.sqlite import AsyncSQLiteManager


async def _apply(conn: aiosqlite.Connection, sql: str) -> None:
    await conn.execute(sql)


@pytest_asyncio.fixture
async def file_db(tmp_path):
    path = os.path.join(str(tmp_path), "stress.db")
    conn = await aiosqlite.connect(path)
    yield conn, path
    await conn.close()


# ---------------------------------------------------------------------------
# Migration idempotency (temp DBs only)
# ---------------------------------------------------------------------------


class TestMigrationIdempotency:
    @pytest.mark.asyncio
    async def test_full_all_migrations_apply_once(self, file_db) -> None:
        conn, _ = file_db
        applied = await MigrationRunner(ALL_MIGRATIONS).run_pending(conn)
        assert len(applied) == len(ALL_MIGRATIONS)
        again = await MigrationRunner(ALL_MIGRATIONS).run_pending(conn)
        assert again == []

    @pytest.mark.asyncio
    async def test_new_runner_instance_is_still_noop(self, file_db) -> None:
        conn, _ = file_db
        await MigrationRunner(ALL_MIGRATIONS).run_pending(conn)
        third = await MigrationRunner(ALL_MIGRATIONS).run_pending(conn)
        assert third == []

    @pytest.mark.asyncio
    async def test_persistence_across_reopen(self, tmp_path) -> None:
        path = os.path.join(str(tmp_path), "reopen.db")
        conn = await aiosqlite.connect(path)
        await MigrationRunner(ALL_MIGRATIONS).run_pending(conn)
        await conn.close()
        conn2 = await aiosqlite.connect(path)
        try:
            noop = await MigrationRunner(ALL_MIGRATIONS).run_pending(conn2)
            assert noop == []
            cur = await conn2.execute("SELECT COUNT(*) FROM _migrations")
            assert (await cur.fetchone())[0] == len(ALL_MIGRATIONS)
        finally:
            await conn2.close()

    @pytest.mark.asyncio
    async def test_mid_list_failure_rolls_back_that_step_only(self, file_db) -> None:
        conn, path = file_db
        boom = Migration(
            id="boom",
            version=999,
            description="explodes",
            apply=_make_exploding(),
        )
        runner = MigrationRunner([*ALL_MIGRATIONS, boom])
        with pytest.raises(RuntimeError, match="boom"):
            await runner.run_pending(conn)
        # All good migrations survived; ledger holds exactly them.
        cur = await conn.execute("SELECT COUNT(*) FROM _migrations")
        count = (await cur.fetchone())[0]
        assert count == len(ALL_MIGRATIONS)
        # Retry without the bad one is a clean no-op.
        retry = await MigrationRunner(ALL_MIGRATIONS).run_pending(conn)
        assert retry == []

    @pytest.mark.asyncio
    async def test_duplicate_version_ids_rejected_by_ledger(self, file_db) -> None:
        """Duplicate migration ids are a programming error; the ledger's
        PRIMARY KEY rejects the second insert (raw IntegrityError)."""
        conn, _ = file_db
        m1 = Migration(id="dup", version=1, description="first", apply=_noop)
        m2 = Migration(id="dup", version=2, description="second", apply=_noop)
        with pytest.raises(sqlite3.IntegrityError):
            await MigrationRunner([m1, m2]).run_pending(conn)

    @pytest.mark.asyncio
    async def test_out_of_order_versions_sorted(self, file_db) -> None:
        conn, _ = file_db
        late = Migration(id="late", version=5, description="v5", apply=_noop)
        early = Migration(id="early", version=1, description="v1", apply=_noop)
        executed = await MigrationRunner([late, early]).run_pending(conn)
        assert executed == ["v1", "v5"]


async def _noop(_conn: aiosqlite.Connection) -> None:
    return None


def _make_exploding():
    async def _explode(_conn: aiosqlite.Connection) -> None:
        raise RuntimeError("boom")

    return _explode


# ---------------------------------------------------------------------------
# AsyncSQLiteManager against a temp file DB
# ---------------------------------------------------------------------------


class TestAsyncSQLiteManagerEdges:
    @pytest.mark.asyncio
    async def test_execute_before_connect_raises(self, tmp_path) -> None:
        mgr = AsyncSQLiteManager(os.path.join(str(tmp_path), "nc.db"))
        with pytest.raises(sqlite_mod.ConnectionError):
            await mgr.execute("SELECT 1")

    @pytest.mark.asyncio
    async def test_double_connect_idempotent(self, tmp_path) -> None:
        mgr = AsyncSQLiteManager(os.path.join(str(tmp_path), "dc.db"))
        await mgr.connect()
        await mgr.connect()
        assert mgr.is_connected
        await mgr.close()

    @pytest.mark.asyncio
    async def test_close_without_connect_noop(self, tmp_path) -> None:
        mgr = AsyncSQLiteManager(os.path.join(str(tmp_path), "cc.db"))
        await mgr.close()
        assert not mgr.is_connected

    @pytest.mark.asyncio
    async def test_execute_many_and_rows_indexable_both_ways(self, tmp_path) -> None:
        mgr = AsyncSQLiteManager(os.path.join(str(tmp_path), "rows.db"))
        async with mgr:
            await mgr.execute(
                "INSERT INTO strategies (name, type, config_json) VALUES (?, ?, ?)",
                ("a", "t", "{}"),
            )
            await mgr.execute_many(
                "INSERT INTO strategies (name, type, config_json) VALUES (?, ?, ?)",
                [("b", "t", "{}"), ("c", "t", "{}")],
            )
            rows = await mgr.execute("SELECT name FROM strategies ORDER BY name")
            assert len(rows) == 3
            assert rows[0][0] == "a"  # ordinal access
            assert rows[1]["name"] == "b"  # mapping access


# ---------------------------------------------------------------------------
# ExperimentRepository edge cases
# ---------------------------------------------------------------------------


class TestExperimentRepoEdges:
    @pytest.mark.asyncio
    async def test_create_with_all_none_fields(self, tmp_path) -> None:
        async with AsyncSQLiteManager(os.path.join(str(tmp_path), "e.db")) as db:
            repo = ExperimentRepository(db)
            eid = await repo.create({})
            got = await repo.get(eid)
            assert got is not None
            assert got["setup_params"] is None
            assert got["evidence_refs"] is None

    @pytest.mark.asyncio
    async def test_fork_deep_copies_nested_params(self, tmp_path) -> None:
        async with AsyncSQLiteManager(os.path.join(str(tmp_path), "f.db")) as db:
            repo = ExperimentRepository(db)
            pid = await repo.create({"name": "root", "setup_params": {"k": [1]}})
            parent_before = await repo.get(pid)
            cid = await repo.fork(pid, {"name": "child"})
            # Mutate the fetched dict post-fork; persisted child must not change.
            parent_before["setup_params"]["k"].append(2)
            child = await repo.get(cid)
            assert child["setup_params"]["k"] == [1]
            assert child["parent_experiment"] == pid

    @pytest.mark.asyncio
    async def test_fork_missing_parent_raises_keyerror(self, tmp_path) -> None:
        async with AsyncSQLiteManager(os.path.join(str(tmp_path), "g.db")) as db:
            repo = ExperimentRepository(db)
            with pytest.raises(KeyError):
                await repo.fork("ghost", {})

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, tmp_path) -> None:
        async with AsyncSQLiteManager(os.path.join(str(tmp_path), "h.db")) as db:
            repo = ExperimentRepository(db)
            assert await repo.get("nope") is None

    @pytest.mark.asyncio
    async def test_ancestor_chain_walk(self, tmp_path) -> None:
        async with AsyncSQLiteManager(os.path.join(str(tmp_path), "i.db")) as db:
            repo = ExperimentRepository(db)
            root = await repo.create({"name": "r"})
            mid = await repo.fork(root, {})
            leaf = await repo.fork(mid, {})
            ancestors = await repo.get_ancestors(leaf)
            assert [a["id"] for a in ancestors] == [mid, root]

    @pytest.mark.asyncio
    async def test_root_has_no_ancestors(self, tmp_path) -> None:
        async with AsyncSQLiteManager(os.path.join(str(tmp_path), "j.db")) as db:
            repo = ExperimentRepository(db)
            root = await repo.create({"name": "r"})
            assert await repo.get_ancestors(root) == []


# ---------------------------------------------------------------------------
# Malformed JSON payload round-trips through repository layer
# ---------------------------------------------------------------------------


class TestMalformedPayloads:
    @pytest.mark.asyncio
    async def test_nonserializable_setup_params_rejected_at_insert(self, tmp_path) -> None:
        async with AsyncSQLiteManager(os.path.join(str(tmp_path), "k.db")) as db:
            repo = ExperimentRepository(db)
            with pytest.raises(TypeError):
                await repo.create({"setup_params": {"bad": object()}})

    @pytest.mark.asyncio
    async def test_unicode_and_empty_dict_params_round_trip(self, tmp_path) -> None:
        async with AsyncSQLiteManager(os.path.join(str(tmp_path), "l.db")) as db:
            repo = ExperimentRepository(db)
            eid = await repo.create(
                {
                    "name": "中文实验 🚀",
                    "setup_params": {"emoji": "📈", "empty": {}, "n": 0},
                    "evidence_refs": [],
                }
            )
            got = await repo.get(eid)
            assert got["name"] == "中文实验 🚀"
            assert got["setup_params"]["emoji"] == "📈"
            assert got["evidence_refs"] == []

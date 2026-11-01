"""Schema migration locks: every core table exists with expected columns,
FKs point at the right parents, lookup indexes exist, reruns are no-ops,
and additive ALTERs succeed post-migration."""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS


@pytest_asyncio.fixture
async def raw():
    c = await aiosqlite.connect(":memory:")
    yield c
    await c.close()


@pytest_asyncio.fixture
async def migrated(raw: aiosqlite.Connection):
    await MigrationRunner(ALL_MIGRATIONS).run_pending(raw)
    return raw


REQUIRED_TABLES = frozenset(
    {
        "strategies",
        "backtests",
        "paper_accounts",
        "paper_orders",
        "paper_positions",
        "audit_logs",
        "strategy_versions",
        "strategy_deployments",
    }
)

REQUIRED_INDEXES = frozenset(
    {
        "idx_strategies_name",
        "idx_backtests_strategy_id",
        "idx_paper_orders_account_id",
        "idx_paper_positions_account_id",
        "idx_audit_logs_timestamp",
        "idx_audit_logs_event_type",
    }
)


async def _column_names(conn: aiosqlite.Connection, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cur.fetchall()}


async def _fk_parents(conn: aiosqlite.Connection, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA foreign_key_list({table})")
    return {row[2] for row in await cur.fetchall()}


@pytest.mark.asyncio
async def test_every_core_table_exists(migrated: aiosqlite.Connection):
    cur = await migrated.execute("SELECT name FROM sqlite_master WHERE type='table'")
    present = {row[0] for row in await cur.fetchall()}
    assert REQUIRED_TABLES <= present


@pytest.mark.asyncio
async def test_rerunning_all_migrations_is_a_noop(raw: aiosqlite.Connection):
    runner = MigrationRunner(ALL_MIGRATIONS)
    assert len(await runner.run_pending(raw)) == len(ALL_MIGRATIONS)
    assert await runner.run_pending(raw) == []


@pytest.mark.asyncio
async def test_strategies_table_columns(migrated: aiosqlite.Connection):
    cols = await _column_names(migrated, "strategies")
    assert {"id", "name", "type", "config_json", "created_at", "updated_at", "version", "lifecycle_state"} <= cols


@pytest.mark.asyncio
async def test_backtests_reference_strategies(migrated: aiosqlite.Connection):
    assert "strategy_version" in await _column_names(migrated, "backtests")
    assert "strategies" in await _fk_parents(migrated, "backtests")


@pytest.mark.asyncio
async def test_paper_orders_reference_accounts(migrated: aiosqlite.Connection):
    assert "paper_accounts" in await _fk_parents(migrated, "paper_orders")


@pytest.mark.asyncio
async def test_audit_logs_table_columns(migrated: aiosqlite.Connection):
    expected = {
        "id",
        "timestamp",
        "event_type",
        "severity",
        "actor",
        "action",
        "resource",
        "outcome",
        "details_json",
    }
    assert expected <= await _column_names(migrated, "audit_logs")


@pytest.mark.asyncio
async def test_lookup_indexes_present(migrated: aiosqlite.Connection):
    cur = await migrated.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")
    built = {row[0] for row in await cur.fetchall()}
    assert REQUIRED_INDEXES <= built


@pytest.mark.asyncio
async def test_appending_tenant_column_to_each_table(migrated: aiosqlite.Connection):
    """tenant_id can be added to every core table without any rebuild."""
    for table in sorted(REQUIRED_TABLES):
        await migrated.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT")
    await migrated.commit()

    assert "tenant_id" in await _column_names(migrated, "strategies")

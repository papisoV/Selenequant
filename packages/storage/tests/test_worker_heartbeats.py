"""P1-2 心跳表仓储测试(验收锚 1-4、9)。

规格锚:internal-plan。
核心:UPSERT 幂等 / mark_stopped / list_stale 45s 判定(ISO 串比较)。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "shared-types"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "storage"))

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.worker_heartbeats import (
    WorkerHeartbeatRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _now_ms() -> int:
    return int(datetime.now(timezone_utc()).timestamp() * 1000)


def timezone_utc():
    return UTC


@pytest_asyncio.fixture
async def repo():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield WorkerHeartbeatRepository(db)


class TestMigration:
    """验收锚 1:迁移建表,PK/CHECK/索引齐。"""

    @pytest.mark.asyncio
    async def test_table_exists_after_migration(self, repo: WorkerHeartbeatRepository):
        rows = await repo._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='worker_heartbeats'"
        )
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_index_exists(self, repo: WorkerHeartbeatRepository):
        rows = await repo._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_worker_heartbeats_role_at'"
        )
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_status_check_constraint(self, repo: WorkerHeartbeatRepository):
        # 非法 status 应被 CHECK 拒绝。
        with pytest.raises(Exception):
            await repo._db.execute(
                "INSERT INTO worker_heartbeats (worker_id, role, status, heartbeat_at) VALUES (?, ?, ?, ?)",
                ("w1", "scheduler", "bogus", _iso(datetime.now(UTC))),
            )


class TestBeat:
    """验收锚 2:UPSERT 幂等。"""

    @pytest.mark.asyncio
    async def test_beat_inserts_row(self, repo: WorkerHeartbeatRepository):
        await repo.beat("scheduler", role="scheduler", metadata={"m": 1})
        rows = await repo._db.execute(
            "SELECT * FROM worker_heartbeats WHERE worker_id='scheduler'"
        )
        assert len(rows) == 1
        assert rows[0]["role"] == "scheduler"
        assert rows[0]["status"] == "running"
        assert rows[0]["metadata_json"] is not None

    @pytest.mark.asyncio
    async def test_beat_twice_is_idempotent(self, repo: WorkerHeartbeatRepository):
        await repo.beat("w", role="scheduler")
        await repo.beat("w", role="scheduler", metadata={"run": 2})
        rows = await repo._db.execute(
            "SELECT * FROM worker_heartbeats WHERE worker_id='w'"
        )
        assert len(rows) == 1  # 仍只有一行
        assert rows[0]["metadata_json"] is not None  # 更新了 metadata


class TestMarkStopped:
    """验收锚 3:mark_stopped 后不再被 stale 捞到。"""

    @pytest.mark.asyncio
    async def test_stopped_worker_not_stale(self, repo: WorkerHeartbeatRepository):
        await repo.beat("w", role="scheduler")
        # 直改 heartbeat_at 到 60s 前,模拟失联。
        old = _iso(datetime.now(UTC) - timedelta(seconds=60))
        await repo._db.execute(
            "UPDATE worker_heartbeats SET heartbeat_at=? WHERE worker_id='w'", (old,)
        )
        await repo.mark_stopped("w")
        stale = await repo.list_stale(_now_ms(), stale_secs=45)
        assert all(w["worker_id"] != "w" for w in stale)


class TestListStale:
    """验收锚 4:45s stale 判定。"""

    @pytest.mark.asyncio
    async def test_stale_older_than_45s(self, repo: WorkerHeartbeatRepository):
        await repo.beat("w", role="scheduler")
        old = _iso(datetime.now(UTC) - timedelta(seconds=60))
        await repo._db.execute(
            "UPDATE worker_heartbeats SET heartbeat_at=? WHERE worker_id='w'", (old,)
        )
        stale = await repo.list_stale(_now_ms(), stale_secs=45)
        assert any(w["worker_id"] == "w" for w in stale)

    @pytest.mark.asyncio
    async def test_fresh_within_45s_not_stale(self, repo: WorkerHeartbeatRepository):
        await repo.beat("w", role="scheduler")
        recent = _iso(datetime.now(UTC) - timedelta(seconds=10))
        await repo._db.execute(
            "UPDATE worker_heartbeats SET heartbeat_at=? WHERE worker_id='w'", (recent,)
        )
        stale = await repo.list_stale(_now_ms(), stale_secs=45)
        assert all(w["worker_id"] != "w" for w in stale)

    @pytest.mark.asyncio
    async def test_stale_orders_oldest_first(self, repo: WorkerHeartbeatRepository):
        await repo.beat("old", role="scheduler")
        await repo.beat("older", role="scheduler")
        # 直改 heartbeat_at:old 40s 前,older 60s 前。
        await repo._db.execute(
            "UPDATE worker_heartbeats SET heartbeat_at=? WHERE worker_id='old'",
            (_iso(datetime.now(UTC) - timedelta(seconds=40)),),
        )
        await repo._db.execute(
            "UPDATE worker_heartbeats SET heartbeat_at=? WHERE worker_id='older'",
            (_iso(datetime.now(UTC) - timedelta(seconds=60)),),
        )
        stale = await repo.list_stale(_now_ms(), stale_secs=45)
        assert [w["worker_id"] for w in stale] == ["older"]  # 只有 60s 的 stale

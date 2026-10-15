"""P1-2 健康探针测试(验收锚 8)。

规格锚:internal-plan。
核心:worker_heartbeats 探针无 stale → healthy;有 stale → degraded 带明细;
经 HealthRegistry 聚合后 /health 组件区能看到该探针状态。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))
_PKG_ROOT = _SERVICE_ROOT.parents[1] / "packages"
sys.path.insert(0, str(_PKG_ROOT / "shared-types"))
sys.path.insert(0, str(_PKG_ROOT / "storage"))

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.worker_heartbeats import (
    WorkerHeartbeatRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

from app.core.dependencies import worker_heartbeats_health  # noqa: E402


@pytest_asyncio.fixture
async def repo():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield WorkerHeartbeatRepository(db)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


class TestHeartbeatHealthProbe:
    """验收锚 8:无 stale → healthy;有 stale → degraded 带 stale 明细。"""

    @pytest.mark.asyncio
    async def test_no_stale_is_healthy(self, repo: WorkerHeartbeatRepository) -> None:
        await repo.beat("scheduler", role="scheduler")
        recent = _iso(datetime.now(UTC) - timedelta(seconds=5))
        await repo._db.execute(
            "UPDATE worker_heartbeats SET heartbeat_at=? WHERE worker_id='scheduler'",
            (recent,),
        )
        probe = worker_heartbeats_health(repo, now_ms=_now_ms(), stale_secs=45)
        result = await probe()
        assert result.name == "worker_heartbeats"
        assert result.status == "healthy"

    @pytest.mark.asyncio
    async def test_stale_is_degraded_with_detail(
        self, repo: WorkerHeartbeatRepository
    ) -> None:
        await repo.beat("scheduler", role="scheduler")
        old = _iso(datetime.now(UTC) - timedelta(seconds=120))
        await repo._db.execute(
            "UPDATE worker_heartbeats SET heartbeat_at=? WHERE worker_id='scheduler'",
            (old,),
        )
        probe = worker_heartbeats_health(repo, now_ms=_now_ms(), stale_secs=45)
        result = await probe()
        assert result.name == "worker_heartbeats"
        assert result.status == "degraded"
        assert result.detail is not None
        assert result.detail["stale"][0]["worker_id"] == "scheduler"
        assert result.detail["stale"][0]["role"] == "scheduler"

    @pytest.mark.asyncio
    async def test_stopped_worker_not_degraded(
        self, repo: WorkerHeartbeatRepository
    ) -> None:
        await repo.beat("scheduler", role="scheduler")
        old = _iso(datetime.now(UTC) - timedelta(seconds=120))
        await repo._db.execute(
            "UPDATE worker_heartbeats SET heartbeat_at=? WHERE worker_id='scheduler'",
            (old,),
        )
        await repo.mark_stopped("scheduler")
        probe = worker_heartbeats_health(repo, now_ms=_now_ms(), stale_secs=45)
        result = await probe()
        assert result.status == "healthy"  # stopped 不算 stale

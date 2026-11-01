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

from app.core.dependencies import (  # noqa: E402
    pa_heartbeat_health,
    worker_heartbeats_health,
)


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


class TestPaHeartbeatProbe:
    """item 8: PA 心跳专用探针(自有节奏),以及通用探针的角色排除。"""

    @pytest.mark.asyncio
    async def test_no_row_is_healthy_not_started(
        self, repo: WorkerHeartbeatRepository
    ) -> None:
        result = await pa_heartbeat_health(repo, now_ms=_now_ms())()
        assert result.status == "healthy"
        assert result.detail["state"] == "not_started"

    @pytest.mark.asyncio
    async def test_fresh_pa_row_is_healthy_with_last_round(
        self, repo: WorkerHeartbeatRepository
    ) -> None:
        await repo.beat(
            "pa_heartbeat",
            role="pa_heartbeat",
            metadata={"last_round": {"day": "2026-10-11", "at": 123.0}},
        )
        result = await pa_heartbeat_health(repo, now_ms=_now_ms())()
        assert result.status == "healthy"
        assert result.detail["last_success_at"] == 123.0
        assert result.detail["last_round"]["day"] == "2026-10-11"

    @pytest.mark.asyncio
    async def test_stalled_pa_row_is_degraded(
        self, repo: WorkerHeartbeatRepository
    ) -> None:
        await repo.beat("pa_heartbeat", role="pa_heartbeat")
        old = _iso(datetime.now(UTC) - timedelta(seconds=600))
        await repo._db.execute(
            "UPDATE worker_heartbeats SET heartbeat_at=? WHERE worker_id='pa_heartbeat'",
            (old,),
        )
        probe = pa_heartbeat_health(repo, now_ms=_now_ms(), stale_secs=180)
        result = await probe()
        assert result.status == "degraded"
        assert result.detail["age_secs"] > 180

    @pytest.mark.asyncio
    async def test_stopped_pa_row_is_healthy(
        self, repo: WorkerHeartbeatRepository
    ) -> None:
        await repo.beat("pa_heartbeat", role="pa_heartbeat")
        old = _iso(datetime.now(UTC) - timedelta(seconds=600))
        await repo._db.execute(
            "UPDATE worker_heartbeats SET heartbeat_at=? WHERE worker_id='pa_heartbeat'",
            (old,),
        )
        await repo.mark_stopped("pa_heartbeat")
        result = await pa_heartbeat_health(repo, now_ms=_now_ms())()
        assert result.status == "healthy"  # 干净退出不算失败

    @pytest.mark.asyncio
    async def test_generic_probe_excludes_pa_role(
        self, repo: WorkerHeartbeatRepository
    ) -> None:
        """PA 角色被排除后,即使其行 stale,通用探针也判 healthy。"""
        await repo.beat("pa_heartbeat", role="pa_heartbeat")
        old = _iso(datetime.now(UTC) - timedelta(seconds=120))
        await repo._db.execute(
            "UPDATE worker_heartbeats SET heartbeat_at=? WHERE worker_id='pa_heartbeat'",
            (old,),
        )
        probe = worker_heartbeats_health(
            repo, now_ms=_now_ms(), stale_secs=45, exclude_roles=("pa_heartbeat",)
        )
        assert (await probe()).status == "healthy"
        # 未排除时同一行判 degraded(证明排除确实起了作用)
        probe_all = worker_heartbeats_health(repo, now_ms=_now_ms(), stale_secs=45)
        assert (await probe_all()).status == "degraded"

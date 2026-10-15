"""P1-2 调度器心跳写入测试(验收锚 5-7)。

规格锚:internal-plan。
核心:注入 repo 后 tick 写心跳 / repo=None 保持纯调度 / 心跳失败不阻塞主循环。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))
_PKG_ROOT = _SERVICE_ROOT.parents[1] / "packages"
sys.path.insert(0, str(_PKG_ROOT / "agent-runtime"))
sys.path.insert(0, str(_PKG_ROOT / "shared-types"))

from app.sentinel_scheduler import SentinelScheduler  # noqa: E402


class _FakeRepo:
    """Duck-typed heartbeat repo recording calls."""

    def __init__(self) -> None:
        self.beat_calls: list[tuple[str, str, str, dict | None]] = []
        self.raise_on_beat = False

    async def beat(
        self,
        worker_id: str,
        *,
        role: str,
        status: str = "running",
        metadata: dict | None = None,
    ) -> None:
        if self.raise_on_beat:
            raise RuntimeError("heartbeat backend down")
        self.beat_calls.append((worker_id, role, status, metadata))


class TestHeartbeatWrite:
    """验收锚 5:注入 repo,跑 tick → 表内出现 scheduler 行。"""

    @pytest.mark.asyncio
    async def test_tick_writes_scheduler_beat(self) -> None:
        repo = _FakeRepo()
        sched = SentinelScheduler(engines={}, llm=None, beat_repo=repo)
        await sched._beat()
        assert len(repo.beat_calls) == 1
        worker_id, role, status, metadata = repo.beat_calls[0]
        assert worker_id == "scheduler"
        assert role == "scheduler"
        assert status == "running"
        assert metadata is not None


class TestNoRepoPureScheduler:
    """验收锚 6:repo=None 时纯调度,不抛异常。"""

    @pytest.mark.asyncio
    async def test_heartbeat_noop_without_repo(self) -> None:
        sched = SentinelScheduler(engines={}, llm=None)
        await sched._beat()  # 不应抛,不应写表


class TestHeartbeatFailureNonBlocking:
    """验收锚 7:repo.beat 抛异常 → tick 仍完成。"""

    @pytest.mark.asyncio
    async def test_beat_failure_swallowed(self) -> None:
        repo = _FakeRepo()
        repo.raise_on_beat = True
        sched = SentinelScheduler(engines={}, llm=None, beat_repo=repo)
        await sched._beat()  # 不应 propagate


class TestRealSchedulerHeartbeat:
    """验收锚 5(真构造):SentinelScheduler 注入 repo,跑 tick → 心跳写入。"""

    @pytest.mark.asyncio
    async def test_real_scheduler_tick_beats(self) -> None:
        repo = _FakeRepo()
        sched = SentinelScheduler(engines={}, llm=None, beat_repo=repo)
        await sched._tick()
        assert len(repo.beat_calls) == 1
        assert repo.beat_calls[0][0] == "scheduler"

    @pytest.mark.asyncio
    async def test_real_scheduler_no_repo_no_crash(self) -> None:
        sched = SentinelScheduler(engines={}, llm=None)
        await sched._tick()  # 不应抛

    @pytest.mark.asyncio
    async def test_real_scheduler_beat_failure_non_blocking(self) -> None:
        repo = _FakeRepo()
        repo.raise_on_beat = True
        sched = SentinelScheduler(engines={}, llm=None, beat_repo=repo)
        await sched._tick()  # 不应抛,主循环继续

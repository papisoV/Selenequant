"""BacktestTaskRepository unit tests (P2-3 acceptance anchors 1-2).

核心:状态机原子迁移——mark_running 仅 pending->running 成功,
mark_completed/mark_failed 仅从 pending|running 迁移成功(False=状态已变),
count_active 只数 pending+running,list_by_user 过滤 user。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.backtest_tasks import (
    BacktestTaskRepository,
    BacktestTaskRow,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def repo():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield BacktestTaskRepository(db)


def _row(
    task_id: str, *, user_id: str = "local", status: str = "pending"
) -> BacktestTaskRow:
    return BacktestTaskRow(
        task_id=task_id,
        user_id=user_id,
        strategy_id="bb_v3",
        strategy_name="布林带",
        symbol="BTC/USDT",
        interval="1h",
        status=status,
        created_at=1711000000000,
    )


@pytest.mark.asyncio
async def test_migration_creates_table_and_check_constraint(
    repo: BacktestTaskRepository,
):
    rows = await repo._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='backtest_tasks'"
    )
    assert len(rows) == 1
    with pytest.raises(Exception):
        await repo._db.execute(
            "INSERT INTO backtest_tasks (task_id, user_id, strategy_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("t-bogus", "local", "bb_v3", "bogus", 1),
        )


@pytest.mark.asyncio
async def test_create_then_get_roundtrips(repo: BacktestTaskRepository):
    await repo.create(_row("t-1", user_id="alice", status="pending"))
    loaded = await repo.get("t-1")
    assert loaded is not None
    assert loaded.task_id == "t-1"
    assert loaded.user_id == "alice"
    assert loaded.strategy_id == "bb_v3"
    assert loaded.strategy_name == "布林带"
    assert loaded.symbol == "BTC/USDT"
    assert loaded.interval == "1h"
    assert loaded.status == "pending"
    assert loaded.created_at == 1711000000000
    assert loaded.error is None
    assert loaded.result_id is None


@pytest.mark.asyncio
async def test_get_unknown_yields_none(repo: BacktestTaskRepository):
    assert await repo.get("ghost") is None


@pytest.mark.asyncio
async def test_get_scoped_to_user_filters_other_users(repo: BacktestTaskRepository):
    await repo.create(_row("t-1", user_id="alice"))
    assert await repo.get("t-1", user_id="alice") is not None
    assert await repo.get("t-1", user_id="bob") is None


@pytest.mark.asyncio
async def test_mark_running_from_pending_succeeds(repo: BacktestTaskRepository):
    await repo.create(_row("t-1"))
    assert await repo.mark_running("t-1", started_at=1711000001000) is True
    loaded = await repo.get("t-1")
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.started_at == 1711000001000


@pytest.mark.asyncio
async def test_mark_running_twice_is_false(repo: BacktestTaskRepository):
    await repo.create(_row("t-1"))
    assert await repo.mark_running("t-1", started_at=1) is True
    assert await repo.mark_running("t-1", started_at=2) is False


@pytest.mark.asyncio
async def test_mark_completed_only_from_running(repo: BacktestTaskRepository):
    await repo.create(_row("t-1"))
    assert await repo.mark_running("t-1", started_at=1) is True
    assert await repo.mark_completed("t-1", result_id="res-1") is True
    loaded = await repo.get("t-1")
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.result_id == "res-1"
    assert loaded.finished_at is not None


@pytest.mark.asyncio
async def test_mark_completed_from_pending_is_false(repo: BacktestTaskRepository):
    """mark_completed 仅从 running 迁移成功(pending 未启动不可直接完成)。"""
    await repo.create(_row("t-1"))
    assert await repo.mark_completed("t-1", result_id="res-1") is False


@pytest.mark.asyncio
async def test_mark_completed_from_completed_is_false(repo: BacktestTaskRepository):
    await repo.create(_row("t-1"))
    await repo.mark_running("t-1", started_at=1)
    await repo.mark_completed("t-1", result_id="res-1")
    assert await repo.mark_completed("t-1", result_id="res-2") is False
    assert await repo.mark_failed("t-1", error="late") is False


@pytest.mark.asyncio
async def test_mark_failed_records_error(repo: BacktestTaskRepository):
    await repo.create(_row("t-1"))
    assert await repo.mark_running("t-1", started_at=1) is True
    assert await repo.mark_failed("t-1", error="boom") is True
    loaded = await repo.get("t-1")
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.error == "boom"
    assert loaded.finished_at is not None


@pytest.mark.asyncio
async def test_mark_failed_from_pending_is_allowed(repo: BacktestTaskRepository):
    """重启恢复路径:pending 任务直接 mark_failed('server restart')。"""
    await repo.create(_row("t-1"))
    assert await repo.mark_failed("t-1", error="server restart") is True


@pytest.mark.asyncio
async def test_mark_unknown_task_is_false(repo: BacktestTaskRepository):
    assert await repo.mark_running("ghost", started_at=1) is False
    assert await repo.mark_completed("ghost", result_id="x") is False
    assert await repo.mark_failed("ghost", error="x") is False


@pytest.mark.asyncio
async def test_count_active_only_counts_pending_and_running(
    repo: BacktestTaskRepository,
):
    await repo.create(_row("t-1", status="pending"))
    await repo.create(_row("t-2", status="pending"))
    await repo.create(_row("t-3", status="completed"))
    await repo.create(_row("t-4", status="failed"))
    await repo.mark_running("t-2", started_at=1)
    assert await repo.count_active("local") == 2


@pytest.mark.asyncio
async def test_count_active_filters_by_user(repo: BacktestTaskRepository):
    await repo.create(_row("t-1", user_id="alice"))
    await repo.create(_row("t-2", user_id="bob"))
    assert await repo.count_active("alice") == 1
    assert await repo.count_active("bob") == 1
    assert await repo.count_active("carol") == 0


@pytest.mark.asyncio
async def test_list_by_user_filters_and_orders_newest_first(
    repo: BacktestTaskRepository,
):
    await repo.create(
        BacktestTaskRow(
            task_id="old", user_id="alice", strategy_id="bb_v3", created_at=100
        )
    )
    await repo.create(
        BacktestTaskRow(
            task_id="new", user_id="alice", strategy_id="bb_v3", created_at=200
        )
    )
    await repo.create(
        BacktestTaskRow(
            task_id="other", user_id="bob", strategy_id="bb_v3", created_at=300
        )
    )
    listed = await repo.list_by_user("alice")
    assert [t.task_id for t in listed] == ["new", "old"]
    assert all(t.user_id == "alice" for t in listed)


@pytest.mark.asyncio
async def test_list_by_user_respects_limit(repo: BacktestTaskRepository):
    for i in range(5):
        await repo.create(_row(f"t-{i}", user_id="alice"))
    assert len(await repo.list_by_user("alice", limit=2)) == 2


@pytest.mark.asyncio
async def test_empty_interval_roundtrips_empty_not_default(
    repo: BacktestTaskRepository,
):
    """空 interval 必须读回空串,不能回填 "1h"。

    列表为 pending/running/failed 任务回退到这张表;若这里把"没存"写成默认
    "1h",一次 4h 回测会永久显示成 1h(用户看到的周期是编造的)。旧行为正是
    ``row["interval"] or "1h"``,这条锁住它不再回来。
    """
    await repo.create(
        BacktestTaskRow(
            task_id="t-bare",
            user_id="local",
            strategy_id="bb_v3",
            symbol="",
            interval="",
            created_at=1,
        )
    )
    loaded = await repo.get("t-bare")
    assert loaded is not None
    assert loaded.interval == ""
    assert loaded.symbol == ""

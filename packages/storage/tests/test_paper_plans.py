"""Tests for PaperPlanRepository (PA 形态实验线 阶段5 — 记录到 paper 计划)."""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.paper_plans import PaperPlanRepository
from selene_space_storage.sqlite import AsyncSQLiteManager


def _sample_plan() -> dict:
    return {
        "experiment_id": "exp-abc123",
        "name": "BTC 双顶 · paper 跟踪",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "direction": "short",
        "entry": 65000.0,
        "stop_loss": 66000.0,
        "take_profit": 61000.0,
        "quantity": 0.001,
        "account_id": "paper-default",
        "status": "planned",
        "note": None,
    }


@pytest_asyncio.fixture
async def repo():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield PaperPlanRepository(db)


@pytest.mark.asyncio
async def test_create_returns_id(repo: PaperPlanRepository):
    pid = await repo.create(_sample_plan())
    assert pid.startswith("plan-")


@pytest.mark.asyncio
async def test_get_roundtrip(repo: PaperPlanRepository):
    pid = await repo.create(_sample_plan())
    plan = await repo.get(pid)
    assert plan is not None
    assert plan["experiment_id"] == "exp-abc123"
    assert plan["symbol"] == "BTC/USDT"
    assert plan["direction"] == "short"
    assert plan["entry"] == 65000.0
    assert plan["status"] == "planned"
    assert plan["created_at"]
    assert plan["updated_at"]


@pytest.mark.asyncio
async def test_get_missing_returns_none(repo: PaperPlanRepository):
    assert await repo.get("plan-nope") is None


@pytest.mark.asyncio
async def test_list_status_filter(repo: PaperPlanRepository):
    await repo.create(_sample_plan())
    active = _sample_plan()
    active["status"] = "active"
    await repo.create(active)

    rows = await repo.list(status="active")
    assert len(rows) == 1
    assert rows[0]["status"] == "active"


@pytest.mark.asyncio
async def test_list_by_experiment(repo: PaperPlanRepository):
    await repo.create(_sample_plan())
    other = _sample_plan()
    other["experiment_id"] = "exp-other"
    await repo.create(other)

    rows = await repo.list_by_experiment("exp-abc123")
    assert len(rows) == 1
    assert rows[0]["experiment_id"] == "exp-abc123"


@pytest.mark.asyncio
async def test_transition_valid_path(repo: PaperPlanRepository):
    pid = await repo.create(_sample_plan())

    # planned -> active
    plan = await repo.transition(pid, "active")
    assert plan is not None
    assert plan["status"] == "active"
    assert plan["updated_at"]

    # active -> completed
    plan = await repo.transition(pid, "completed")
    assert plan is not None
    assert plan["status"] == "completed"

    # completed 不再可迁移
    assert await repo.transition(pid, "cancelled") is None


@pytest.mark.asyncio
async def test_transition_invalid_status(repo: PaperPlanRepository):
    pid = await repo.create(_sample_plan())
    # 非白名单状态 → None
    assert await repo.transition(pid, "banana") is None
    # 跳过 planned 直接到 cancelled 合法(planned 允许 cancelled)
    assert await repo.transition(pid, "cancelled") is not None


@pytest.mark.asyncio
async def test_transition_missing_plan(repo: PaperPlanRepository):
    assert await repo.transition("plan-nope", "active") is None


@pytest.mark.asyncio
async def test_transition_guarded_against_concurrent_status_change(
    repo: PaperPlanRepository,
):
    """Guarded transition 形态: read-then-write 间的并发跳变返回 None.

    竞态窗口在 transition() 内部 get() 与 UPDATE 之间: 让第一次 get() 返回
    旧快照(planned), 并发写者此时直接 SQL 把 status 推到 cancelled;
    guarded UPDATE (WHERE status=planned) 不命中变 no-op, transition 必须
    返回 None 而非静默把 cancelled 覆盖成 active。
    """
    pid = await repo.create(_sample_plan())
    real_get = repo.get
    snapshot = await real_get(pid)  # T0 读: status=planned

    calls = {"n": 0}

    async def stale_then_real(plan_id: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return snapshot
        return await real_get(plan_id)

    repo.get = stale_then_real  # type: ignore[method-assign]
    try:
        # 并发写者绕过 repo 状态机直改 DB(planned -> cancelled)
        await repo._db.execute(
            "UPDATE paper_plans SET status = 'cancelled' WHERE id = ?", (pid,)
        )
        # guarded UPDATE 的 WHERE status='planned' 不命中 → 返回 None
        assert await repo.transition(pid, "active") is None
    finally:
        repo.get = real_get  # type: ignore[method-assign]

    plan = await real_get(pid)
    assert plan is not None
    assert plan["status"] == "cancelled"

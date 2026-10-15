"""Backtest task persistence integration tests (P2-3 acceptance anchors 3-4).

接入测试:POST /backtests 先落 SQLite pending 再跑后台任务;
任务完成 → running→completed 带 result_id;异常 → failed 带 error;
lifespan 恢复:pending/running → failed('server restart')。
用 :memory: SQLite + set_db_manager 注入,避免触碰 data/klines.db。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import app.api.v1.backtests as backtests_mod
import pytest
import pytest_asyncio
from app.core.dependencies import set_db_manager
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.backtest_tasks import (
    BacktestTaskRepository,
    BacktestTaskRow,
)
from selene_space_storage.sqlite import AsyncSQLiteManager
from selene_space_types.strategy import BacktestMetrics, BacktestResult


@pytest_asyncio.fixture
async def db_manager():
    runner = MigrationRunner(ALL_MIGRATIONS)
    db = AsyncSQLiteManager(":memory:", migration_runner=runner)
    await db.connect()
    set_db_manager(db)
    yield db
    set_db_manager(None)
    await db.close()


@pytest.fixture(autouse=True)
def _wipe_tasks():
    backtests_mod._tasks.clear()
    yield
    backtests_mod._tasks.clear()


def _sample_result(task_id: str) -> BacktestResult:
    return BacktestResult(
        id=task_id,
        strategy_id="strat-fake",
        strategy_version=1,
        metrics=BacktestMetrics(
            total_return=0.15,
            annual_return=0.45,
            sharpe_ratio=1.8,
            max_drawdown=-0.08,
            win_rate=0.55,
            profit_factor=1.6,
            total_trades=42,
        ),
        equity_curve=[10000.0, 10100.0, 10500.0],
        trades=[{"pnl": 10.0, "side": "buy", "timestamp": 1711000000000}],
        trades_count=1,
        created_at=1711000000000,
        start_date=datetime(2025, 1, 1, tzinfo=UTC),
        end_date=datetime(2025, 3, 31, tzinfo=UTC),
    )


@asynccontextmanager
async def api():
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_post_persists_pending_row_before_run(db_manager):
    """POST /backtests 落 pending 行(后台任务还没跑)。"""
    release = asyncio.Event()

    async def _blocking_runner(task, body):
        task.status = backtests_mod.BacktestTaskStatus.RUNNING
        await release.wait()

    original = backtests_mod._run_backtest
    backtests_mod._run_backtest = _blocking_runner
    try:
        async with api() as c:
            resp = await c.post(
                "/api/v1/backtests",
                json={"initial_cash": 10000, "strategy_id": "strat-abc"},
            )
        assert resp.status_code == 202
        task_id = resp.json()["data"]["task_id"]

        repo = BacktestTaskRepository(db_manager)
        row = await repo.get(task_id)
        assert row is not None
        assert row.status == "pending"
        assert row.strategy_id == "strat-abc"
        assert row.user_id == "local"
    finally:
        release.set()
        backtests_mod._run_backtest = original
        await asyncio.sleep(0.05)  # let the blocked task finish


@pytest.mark.asyncio
async def test_post_persists_requested_symbol_and_interval(db_manager):
    """POST 必须把请求里的 symbol/interval 落到任务行。

    列表对 pending/running/failed 回退到这张表,而这张表比进程活得久:漏存时
    一行 4h 的 ETH 回测会以 symbol="" / interval="1h"(列默认值)永久呈现,
    用户看到的是编造的周期。与前端"周期/区间"两列可区分性直接相关。
    """
    blocked = asyncio.Event()
    original = backtests_mod._run_backtest

    async def _block(task, body):
        await blocked.wait()

    backtests_mod._run_backtest = _block
    try:
        async with api() as c:
            resp = await c.post(
                "/api/v1/backtests",
                json={
                    "initial_cash": 10000,
                    "strategy_id": "strat-abc",
                    "symbol": "ETH/USDT",
                    "interval": "4h",
                },
            )
        assert resp.status_code == 202
        task_id = resp.json()["data"]["task_id"]

        repo = BacktestTaskRepository(db_manager)
        row = await repo.get(task_id)
        assert row is not None
        assert row.symbol == "ETH/USDT"
        assert row.interval == "4h"
    finally:
        blocked.set()
        backtests_mod._run_backtest = original
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_omitted_symbol_interval_persist_empty_not_default(db_manager):
    """未指定 symbol/interval 时存空串,而不是伪造列默认值 "1h"。

    策略配置的兜底发生在后台 runner 里(改内存 task,不回写行);请求本身没给
    坐标时,行就该诚实为空,列表显示 "—"。
    """
    blocked = asyncio.Event()
    original = backtests_mod._run_backtest

    async def _block(task, body):
        await blocked.wait()

    backtests_mod._run_backtest = _block
    try:
        async with api() as c:
            resp = await c.post(
                "/api/v1/backtests",
                json={"initial_cash": 10000, "strategy_id": "strat-abc"},
            )
        task_id = resp.json()["data"]["task_id"]

        repo = BacktestTaskRepository(db_manager)
        row = await repo.get(task_id)
        assert row is not None
        assert row.symbol == ""
        assert row.interval == ""
    finally:
        blocked.set()
        backtests_mod._run_backtest = original
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_completion_marks_running_then_completed_with_result_id(db_manager):
    """后台任务完成 → SQLite 行 running→completed 带 result_id。"""

    async def _fake_success(task, body):
        repo = backtests_mod._get_task_repo()
        task.status = backtests_mod.BacktestTaskStatus.RUNNING
        if repo is not None:
            await repo.mark_running(
                task.task_id, int(asyncio.get_event_loop().time() * 1000)
            )
        task.strategy_name = "Fake Strategy"
        task.symbol = "BTC/USDT"
        task.interval = "1h"
        result = _sample_result(task.task_id)
        task.result = result
        task.status = backtests_mod.BacktestTaskStatus.COMPLETED
        if repo is not None:
            await repo.mark_completed(task.task_id, result.id)

    original = backtests_mod._run_backtest
    backtests_mod._run_backtest = _fake_success
    try:
        async with api() as c:
            resp = await c.post(
                "/api/v1/backtests",
                json={"initial_cash": 10000, "strategy_id": "strat-fake"},
            )
        task_id = resp.json()["data"]["task_id"]
        await asyncio.sleep(0.1)  # let the fake runner finish

        repo = BacktestTaskRepository(db_manager)
        row = await repo.get(task_id)
        assert row is not None
        assert row.status == "completed"
        assert row.result_id == task_id
        assert row.started_at is not None
        assert row.finished_at is not None
    finally:
        backtests_mod._run_backtest = original


@pytest.mark.asyncio
async def test_exception_marks_failed_with_error(db_manager):
    """后台任务异常 → SQLite 行 failed 带 error(真实 _run_backtest 未知策略路径)。"""
    async with api() as c:
        resp = await c.post(
            "/api/v1/backtests",
            json={"initial_cash": 10000, "strategy_id": "strat-unknown"},
        )
    task_id = resp.json()["data"]["task_id"]

    repo = BacktestTaskRepository(db_manager)
    for _ in range(50):
        row = await repo.get(task_id)
        if row is not None and row.status == "failed":
            break
        await asyncio.sleep(0.02)
    assert row is not None
    assert row.status == "failed"
    assert row.error and "not found" in row.error
    assert row.finished_at is not None


@pytest.mark.asyncio
async def test_restart_recovery_fails_pending_and_running(db_manager):
    """模拟进程重启:lifespan 恢复把 pending/running 全部标记 failed('server restart')。"""
    repo = BacktestTaskRepository(db_manager)
    await repo.create(
        BacktestTaskRow(
            task_id="orphan-pending",
            user_id="local",
            strategy_id="bb_v3",
            status="pending",
            created_at=1711000000000,
        )
    )
    await repo.create(
        BacktestTaskRow(
            task_id="orphan-running",
            user_id="local",
            strategy_id="bb_v3",
            status="running",
            created_at=1711000000000,
            started_at=1711000001000,
        )
    )
    await repo.create(
        BacktestTaskRow(
            task_id="done-completed",
            user_id="local",
            strategy_id="bb_v3",
            status="completed",
            created_at=1711000000000,
            result_id="done-completed",
        )
    )

    await backtests_mod._recover_orphaned_tasks()

    pending = await repo.get("orphan-pending")
    running = await repo.get("orphan-running")
    completed = await repo.get("done-completed")
    assert pending is not None and pending.status == "failed"
    assert pending.error == "server restart"
    assert running is not None and running.status == "failed"
    assert running.error == "server restart"
    assert completed is not None and completed.status == "completed"


@pytest.mark.asyncio
async def test_get_falls_back_to_sqlite_when_not_in_memory(db_manager):
    """内存 _tasks 查不到 → get_backtest 回退 SQLite 任务表。"""
    repo = BacktestTaskRepository(db_manager)
    await repo.create(
        BacktestTaskRow(
            task_id="bt-persisted",
            user_id="local",
            strategy_id="bb_v3",
            status="failed",
            error="server restart",
            created_at=1711000000000,
        )
    )
    async with api() as c:
        resp = await c.get("/api/v1/backtests/bt-persisted")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == "bt-persisted"
    assert data["status"] == "failed"
    assert data["error"] == "server restart"


@pytest.mark.asyncio
async def test_completed_task_serves_rich_result_over_thin_task_row(db_manager):
    """回归: completed 任务的 result_id == task_id 时, 详情必须返回 backtests 表的
    真实指标, 不能被同 id 的瘦任务行遮蔽(此前 fallback 先查任务表 → 9 字段空壳,
    前端详情页指标全 0、曲线消失)。
    """
    from selene_space_storage.repositories.backtests import BacktestRepository

    task_id = "bt-rich"
    result_repo = BacktestRepository(db_manager)
    await result_repo.save(_sample_result(task_id), user_id="local")

    task_repo = BacktestTaskRepository(db_manager)
    await task_repo.create(
        BacktestTaskRow(
            task_id=task_id,
            user_id="local",
            strategy_id="strat-fake",
            status="completed",
            created_at=1711000000000,
            result_id=task_id,
        )
    )

    async with api() as c:
        resp = await c.get(f"/api/v1/backtests/{task_id}")
        listed = await c.get("/api/v1/backtests")

    assert resp.status_code == 200
    data = resp.json()["data"]
    # 富结果必须赢: 指标/成交/曲线都在
    assert data["status"] == "completed"
    assert data["sharpe_ratio"] == 1.8
    assert data["total_return"] == 0.15
    assert data["total_trades"] == 42
    assert len(data["equity_curve"]) == 3
    assert len(data["trades"]) == 1

    # 列表里同 id 只出现一次, 且是富行(带 sharpe/trades)
    rows = [r for r in listed.json()["data"] if r["id"] == task_id]
    assert len(rows) == 1
    assert rows[0].get("sharpe_ratio") == 1.8


@pytest.mark.asyncio
async def test_listing_hydrates_strategy_name_from_strategies_table(db_manager):
    """列表行的 strategy_name 为占位(空/回显 id)时, 从 strategies 表回填人类名。"""
    from selene_space_types.strategy import StrategyConfig, StrategyType

    # 落一行策略, 名字与 id 不同
    await db_manager.execute(
        "INSERT INTO strategies (id, name, type, config_json, created_at, updated_at)"
        " VALUES (?, ?, 'custom', ?, datetime('now'), datetime('now'))",
        (
            "bb_v3",
            "布林带升级版",
            StrategyConfig(
                id="bb_v3",
                name="布林带升级版",
                type=StrategyType.CUSTOM,
                symbols=[],
                interval="1d",
            ).model_dump_json(),
        ),
    )
    repo = BacktestTaskRepository(db_manager)
    await repo.create(
        BacktestTaskRow(
            task_id="bt-named",
            user_id="local",
            strategy_id="bb_v3",
            strategy_name="",
            status="failed",
            error="x",
            created_at=1711000000000,
        )
    )
    async with api() as c:
        listed = await c.get("/api/v1/backtests")
    rows = [r for r in listed.json()["data"] if r["id"] == "bt-named"]
    assert len(rows) == 1
    assert rows[0]["strategy_name"] == "布林带升级版"


@pytest.mark.asyncio
async def test_list_returns_light_rows_without_curves(db_manager):
    """列表端点必须回轻量行:带指标、不带曲线/成交。

    列表首次真要加载全部行时,每行都带上曲线(50 行 ≈ 6MB)会把请求推过
    前端 12s abort → 页面空。轻量行只保留表格渲染所需的标量字段。
    """
    from selene_space_storage.repositories.backtests import BacktestRepository

    result_repo = BacktestRepository(db_manager)
    await result_repo.save(_sample_result("bt-light"), user_id="local")

    async with api() as c:
        listed = await c.get("/api/v1/backtests")

    rows = [r for r in listed.json()["data"] if r["id"] == "bt-light"]
    assert len(rows) == 1
    row = rows[0]
    # 标量/指标仍在
    assert row["status"] == "completed"
    assert row["sharpe_ratio"] == 1.8
    assert row["total_return"] == 0.15
    assert row["total_trades"] == 42
    # 曲线/成交/配置不得随列表下发
    for heavy in (
        "equity_curve",
        "trades",
        "drawdown_curve",
        "buy_hold_curve",
        "exposure_curve",
        "config_snapshot",
        "timestamps",
        "result",
    ):
        assert heavy not in row, f"列表行不应含重字段 {heavy}"


@pytest.mark.asyncio
async def test_detail_still_returns_full_curves(db_manager):
    """详情端点必须保留完整曲线/成交 —— 轻量化只针对列表。"""
    from selene_space_storage.repositories.backtests import BacktestRepository

    result_repo = BacktestRepository(db_manager)
    await result_repo.save(_sample_result("bt-full"), user_id="local")

    async with api() as c:
        resp = await c.get("/api/v1/backtests/bt-full")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["sharpe_ratio"] == 1.8
    assert data["equity_curve"] == [10000.0, 10100.0, 10500.0]
    assert len(data["trades"]) == 1
    assert "config_snapshot" in data

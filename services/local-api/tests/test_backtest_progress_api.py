"""US-105 回测进度透传: RUNNING 态 GET /backtests/{task_id} 带 progress(0-100),终态不透传。

进度不落库(内存 task.progress,engine progress_cb 线程内 setattr),所以这里
直接构造 task 注入 _tasks 验读路径;engine 侧回调语义由 backtest-engine 包
的 test_backtest_progress.py 覆盖。
"""

from __future__ import annotations

import app.api.v1.backtests as backtests_mod
import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _wipe_tasks():
    backtests_mod._tasks.clear()
    yield
    backtests_mod._tasks.clear()


def _seed_task(status: backtests_mod.BacktestTaskStatus, progress: int) -> str:
    task = backtests_mod.BacktestTask(
        task_id="bt-progress-x1",
        strategy_id="strat-p1",
        status=status,
        progress=progress,
    )
    backtests_mod._tasks[task.task_id] = task
    return task.task_id


async def _get_task(c: AsyncClient, task_id: str) -> dict:
    resp = await c.get(f"/api/v1/backtests/{task_id}")
    assert resp.status_code == 200, resp.text
    data: dict = resp.json()["data"]
    return data


@pytest.mark.asyncio
async def test_running_task_exposes_progress():
    _seed_task(backtests_mod.BacktestTaskStatus.RUNNING, 42)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        data = await _get_task(c, "bt-progress-x1")
    assert data["status"] == "running"
    assert data["progress"] == 42


@pytest.mark.asyncio
async def test_terminal_states_do_not_expose_progress():
    _seed_task(backtests_mod.BacktestTaskStatus.COMPLETED, 100)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        data = await _get_task(c, "bt-progress-x1")
    assert data["status"] == "completed"
    assert "progress" not in data


@pytest.mark.asyncio
async def test_pending_task_progress_is_zero():
    _seed_task(backtests_mod.BacktestTaskStatus.PENDING, 0)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        data = await _get_task(c, "bt-progress-x1")
    assert data["status"] == "pending"
    assert data["progress"] == 0

"""透明性批2-7: tuning_tasks 落库往返验证。

- v024 迁移建表
- _persist_tuning_task UPSERT → _load_persisted_tuning_task 还原一致
- _list_persisted_tuning_tasks 返回全部
- get_tuning_task 内存 miss 时回库(重启恢复路径)
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.api.v1 import research as research_mod
from app.core import dependencies as deps

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as mgr:
        deps.set_db_manager(mgr)
        yield mgr
        deps.set_db_manager(None)


def _make_task(task_id: str = "tune-abc123") -> research_mod.TuningTaskState:
    return research_mod.TuningTaskState(
        task_id=task_id,
        status=research_mod.TuningTaskStatus.RUNNING,
        config={"symbol": "BTC/USDT", "trials": 30},
        progress=research_mod.TuningProgress(current=3, total=9),
    )


@pytest.mark.asyncio
async def test_persist_load_roundtrip(db):
    task = _make_task()
    await research_mod._persist_tuning_task(task)
    loaded = await research_mod._load_persisted_tuning_task(task.task_id)
    assert loaded is not None
    assert loaded.task_id == task.task_id
    assert loaded.status == research_mod.TuningTaskStatus.RUNNING
    assert loaded.config == {"symbol": "BTC/USDT", "trials": 30}
    assert loaded.progress.current == 3


@pytest.mark.asyncio
async def test_upsert_updates_status(db):
    task = _make_task()
    await research_mod._persist_tuning_task(task)
    task.status = research_mod.TuningTaskStatus.COMPLETED
    task.result_file = "tuning_x.json"
    await research_mod._persist_tuning_task(task)
    loaded = await research_mod._load_persisted_tuning_task(task.task_id)
    assert loaded is not None
    assert loaded.status == research_mod.TuningTaskStatus.COMPLETED
    assert loaded.result_file == "tuning_x.json"


@pytest.mark.asyncio
async def test_list_persisted(db):
    await research_mod._persist_tuning_task(_make_task("tune-a"))
    await research_mod._persist_tuning_task(_make_task("tune-b"))
    tasks = await research_mod._list_persisted_tuning_tasks()
    assert {t.task_id for t in tasks} == {"tune-a", "tune-b"}


@pytest.mark.asyncio
async def test_get_endpoint_falls_back_to_db(db):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    task = _make_task("tune-cold")  # 只落库,不进内存字典
    await research_mod._persist_tuning_task(task)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/api/v1/research/tuning/tasks/tune-cold")
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None
    assert body["data"]["task_id"] == "tune-cold"
    assert body["data"]["status"] == "running"

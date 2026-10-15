"""Episode ledger API tests (S7 acceptance).

Two load-bearing properties:

1. A completed backtest (via the real ``_record_episode`` hook) leaves one
   RECORDED episode row visible through GET /api/v1/episodes, with kind
   derived from the strategy_id prefix and metrics carried through.
2. An episode-write failure must not fail the backtest itself: the hook
   swallows everything into a warning, so the task still reports COMPLETED.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import app.api.v1.backtests as backtests_mod
import pytest
import pytest_asyncio
from app.core.dependencies import set_db_manager, set_episode_repo
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.episodes import EpisodeRepository
from selene_space_storage.sqlite import AsyncSQLiteManager
from selene_space_types.strategy import BacktestMetrics, BacktestResult


@pytest_asyncio.fixture
async def db_manager():
    runner = MigrationRunner(ALL_MIGRATIONS)
    db = AsyncSQLiteManager(":memory:", migration_runner=runner)
    await db.connect()
    set_db_manager(db)
    # ASGITransport doesn't run lifespan, so mirror its set_episode_repo here.
    set_episode_repo(EpisodeRepository(db))
    yield db
    set_episode_repo(None)
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
        strategy_id="bridge:FAKEUSDT_autogen_factor",
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
async def test_completed_backtest_leaves_recorded_episode(db_manager):
    """Backtest completes via the real hook → GET /episodes shows RECORDED row."""

    async def _fake_success(task, body):
        task.status = backtests_mod.BacktestTaskStatus.RUNNING
        task.strategy_name = "Bridge: FAKE"
        task.symbol = "FAKE/USDT"
        task.interval = "1h"
        result = _sample_result(task.task_id)
        task.result = result
        task.status = backtests_mod.BacktestTaskStatus.COMPLETED
        await backtests_mod._record_episode(task, body, result)

    original = backtests_mod._run_backtest
    backtests_mod._run_backtest = _fake_success
    try:
        async with api() as c:
            resp = await c.post(
                "/api/v1/backtests",
                json={"strategy_id": "bridge:FAKEUSDT_autogen_factor"},
            )
            task_id = resp.json()["data"]["task_id"]
            await asyncio.sleep(0.1)

            listing = await c.get("/api/v1/episodes", params={"limit": 10})
    finally:
        backtests_mod._run_backtest = original

    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) >= 1
    top = rows[0]
    assert top["strategy_kind"] == "bridge"
    assert top["strategy_id"] == "bridge:FAKEUSDT_autogen_factor"
    assert top["provenance"] == "RECORDED"
    assert top["backtest_id"] == task_id
    assert top["metrics"]["sharpe_ratio"] == 1.8
    assert top["symbol"] == "FAKE/USDT"


@pytest.mark.asyncio
async def test_episode_write_failure_does_not_fail_backtest(db_manager):
    """Episode repo raising must leave the backtest task COMPLETED."""

    async def _fake_success(task, body):
        task.status = backtests_mod.BacktestTaskStatus.RUNNING
        task.strategy_name = "Bridge: FAKE"
        task.symbol = "FAKE/USDT"
        task.interval = "1h"
        result = _sample_result(task.task_id)
        task.result = result
        task.status = backtests_mod.BacktestTaskStatus.COMPLETED
        await backtests_mod._record_episode(task, body, result)
        # The task status must stay COMPLETED even though the hook below raised.

    original = backtests_mod._run_backtest
    backtests_mod._run_backtest = _fake_success
    try:
        with patch.object(
            EpisodeRepository, "create", side_effect=RuntimeError("boom")
        ):
            async with api() as c:
                resp = await c.post(
                    "/api/v1/backtests",
                    json={"strategy_id": "bridge:FAKEUSDT_autogen_factor"},
                )
                await asyncio.sleep(0.1)

                task_id = resp.json()["data"]["task_id"]
                detail = await c.get(f"/api/v1/backtests/{task_id}")
    finally:
        backtests_mod._run_backtest = original

    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "completed"


@pytest.mark.asyncio
async def test_kind_derivation_for_each_prefix(db_manager):
    """alpha:/qd:/other prefixes map to alpha/qd/yaml kinds."""
    repo = EpisodeRepository(db_manager)
    await repo.create(
        strategy_kind="alpha", strategy_id="alpha:x", symbol="A/USDT", interval="1h"
    )
    await repo.create(
        strategy_kind="qd", strategy_id="qd:src48", symbol="B/USDT", interval="1h"
    )
    await repo.create(
        strategy_kind="yaml", strategy_id="bb_v3", symbol="C/USDT", interval="1h"
    )

    async with api() as c:
        for kind in ("alpha", "qd", "yaml"):
            resp = await c.get("/api/v1/episodes", params={"strategy_kind": kind})
            rows = resp.json()
            assert len(rows) == 1, kind
            assert rows[0]["strategy_kind"] == kind


@pytest.mark.asyncio
async def test_get_episode_detail_and_404(db_manager):
    repo = EpisodeRepository(db_manager)
    eid = await repo.create(
        strategy_kind="bridge",
        strategy_id="bridge:FAKEUSDT_autogen_factor",
        symbol="FAKE/USDT",
        interval="1h",
        metrics={"sharpe_ratio": 2.5},
    )

    async with api() as c:
        detail = await c.get(f"/api/v1/episodes/{eid}")
        assert detail.status_code == 200
        assert detail.json()["metrics"]["sharpe_ratio"] == 2.5

        missing = await c.get("/api/v1/episodes/epi-nonexistent")
        assert missing.status_code == 404

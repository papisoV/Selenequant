"""World Model aggregation endpoint tests (L3-1 acceptance).

Three load-bearing properties:

1. Empty ledger → groups=[] + generated_at present (empty World Model is a
   legal state, not 404/500).
2. Seeded rows group correctly by (symbol, interval): counts, win_rate,
   best-by-annual_return with its strategy_id, sharpe stats.
3. A NaN-bearing metrics_json (json.dumps(float('nan')) is invalid strict
   JSON) must not crash the endpoint — json_valid yields NULL instead.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from app.core.dependencies import set_db_manager, set_episode_repo
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.episodes import EpisodeRepository
from selene_space_storage.sqlite import AsyncSQLiteManager


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


@asynccontextmanager
async def api():
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        yield c


def _seed(
    repo: EpisodeRepository,
    *,
    strategy_id: str,
    symbol: str,
    interval: str,
    metrics: dict | None = None,
    kind: str = "qd",
):
    return repo.create(
        strategy_kind=kind,
        strategy_id=strategy_id,
        symbol=symbol,
        interval=interval,
        metrics=metrics,
    )


@pytest.mark.asyncio
async def test_empty_ledger_returns_empty_groups(db_manager):
    async with api() as c:
        resp = await c.get("/api/v1/episodes/world-model")
    assert resp.status_code == 200
    body = resp.json()
    assert body["groups"] == []
    assert body["total_episodes"] == 0
    assert body["generated_at"]


@pytest.mark.asyncio
async def test_grouping_best_and_win_rate(db_manager):
    repo = EpisodeRepository(db_manager)
    await _seed(
        repo,
        strategy_id="qd:src1",
        symbol="BTC/USDT",
        interval="1h",
        metrics={"annual_return": 0.65, "sharpe_ratio": 2.3},
    )
    await _seed(
        repo,
        strategy_id="bridge:ARBUSDT_autogen_factor",
        symbol="BTC/USDT",
        interval="1h",
        kind="bridge",
        metrics={"annual_return": 0.59, "sharpe_ratio": 1.6},
    )
    await _seed(
        repo,
        strategy_id="qd:src48",
        symbol="ETH/USDT",
        interval="4h",
        metrics={"annual_return": -0.37, "sharpe_ratio": -0.4},
    )

    async with api() as c:
        resp = await c.get("/api/v1/episodes/world-model")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_episodes"] == 3
    groups = {(g["symbol"], g["interval"]): g for g in body["groups"]}
    assert len(groups) == 2

    btc = groups[("BTC/USDT", "1h")]
    assert btc["episode_count"] == 2
    assert btc["winning_count"] == 2
    assert btc["win_rate"] == 1.0
    assert sorted(btc["kinds"]) == ["bridge", "qd"]
    assert btc["best"]["strategy_id"] == "qd:src1"
    assert btc["best"]["annual_return"] == 0.65
    assert btc["best"]["sharpe_ratio"] == 2.3
    assert btc["best_sharpe"] == 2.3
    assert btc["worst_sharpe"] == 1.6
    assert btc["avg_sharpe"] == pytest.approx(1.95)
    assert btc["last_episode_at"]

    eth = groups[("ETH/USDT", "4h")]
    assert eth["episode_count"] == 1
    assert eth["winning_count"] == 0
    assert eth["win_rate"] == 0.0
    assert eth["best"]["strategy_id"] == "qd:src48"


@pytest.mark.asyncio
async def test_invalid_metrics_json_does_not_crash(db_manager):
    """json.dumps(float('nan')) is invalid strict JSON — row must degrade to
    NULL metrics, not raise. It still counts toward episode_count."""
    repo = EpisodeRepository(db_manager)
    await repo.create(
        strategy_kind="bridge",
        strategy_id="bridge:FAKEUSDT_autogen_factor",
        symbol="FAKE/USDT",
        interval="1h",
        metrics={"annual_return": float("nan"), "sharpe_ratio": float("nan")},
    )

    async with api() as c:
        resp = await c.get("/api/v1/episodes/world-model")
    assert resp.status_code == 200
    groups = resp.json()["groups"]
    assert len(groups) == 1
    g = groups[0]
    assert g["episode_count"] == 1
    # NaN annual_return is not > 0, so not a winner; sharpe stats all NULL.
    assert g["winning_count"] == 0
    assert g["best"] is None
    assert g["best_sharpe"] is None
    assert g["worst_sharpe"] is None
    assert g["avg_sharpe"] is None

"""Tests for the episodes migration (v022) + EpisodeRepository (S5/S6).

覆盖:fresh 库建表列齐全、旧库(只到 v015)升级路径、CRUD 写→列→取
往返、metrics_json 透传、strategy_kind 过滤。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.episodes import EpisodeRepository
from selene_space_storage.sqlite import AsyncSQLiteManager

_EPISODE_COLUMNS = {
    "id",
    "strategy_kind",
    "strategy_id",
    "strategy_name",
    "symbol",
    "interval",
    "exchange",
    "market_type",
    "params_json",
    "backtest_id",
    "metrics_json",
    "provenance",
    "created_at",
}


@pytest_asyncio.fixture
async def repo():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield EpisodeRepository(db)


@pytest.mark.asyncio
async def test_episodes_table_columns_complete():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        rows = await db.execute("PRAGMA table_info(episodes)")
        names = {r["name"] for r in rows}
        assert _EPISODE_COLUMNS <= names
        assert len(rows) == len(_EPISODE_COLUMNS)


@pytest.mark.asyncio
async def test_legacy_db_upgrades_to_v022(tmp_path):
    """旧库(只注册到 v015)先建库,再以完整注册表重开 → v022 补建 episodes。"""
    from selene_space_storage.migrations_pkg.v001_initial import migration as m001
    from selene_space_storage.migrations_pkg.v002_strategy_versions import (
        migration as m002,
    )
    from selene_space_storage.migrations_pkg.v003_chat_sessions import (
        migration as m003,
    )
    from selene_space_storage.migrations_pkg.v004_backtest_curves import (
        migration as m004,
    )
    from selene_space_storage.migrations_pkg.v005_backtest_symbol_interval import (
        migration as m005,
    )
    from selene_space_storage.migrations_pkg.v006_user_id import migration as m006
    from selene_space_storage.migrations_pkg.v007_live_trading import (
        migration as m007,
    )
    from selene_space_storage.migrations_pkg.v008_events_system_logs import (
        migration as m008,
    )
    from selene_space_storage.migrations_pkg.v009_analysis_records import (
        migration as m009,
    )
    from selene_space_storage.migrations_pkg.v010_experiments import (
        migration as m010,
    )
    from selene_space_storage.migrations_pkg.v011_paper_plans import (
        migration as m011,
    )
    from selene_space_storage.migrations_pkg.v012_experiment_verification import (
        migration as m012,
    )
    from selene_space_storage.migrations_pkg.v013_worker_heartbeats import (
        migration as m013,
    )
    from selene_space_storage.migrations_pkg.v014_backtest_tasks import (
        migration as m014,
    )
    from selene_space_storage.migrations_pkg.v015_research_missions import (
        migration as m015,
    )

    legacy = MigrationRunner(
        [
            m001,
            m002,
            m003,
            m004,
            m005,
            m006,
            m007,
            m008,
            m009,
            m010,
            m011,
            m012,
            m013,
            m014,
            m015,
        ]
    )

    db_path = tmp_path / "legacy.db"
    async with AsyncSQLiteManager(db_path, migration_runner=legacy) as db:
        pass  # 旧 runner 建库到 v015

    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(db_path, migration_runner=runner) as db:
        rows = await db.execute("PRAGMA table_info(episodes)")
        names = {r["name"] for r in rows}
        assert _EPISODE_COLUMNS <= names
        ledger = await db.execute("SELECT version FROM _migrations ORDER BY version")
        versions = [r["version"] for r in ledger]
        assert versions == sorted(m.version for m in ALL_MIGRATIONS)


@pytest.mark.asyncio
async def test_create_get_roundtrip(repo: EpisodeRepository):
    eid = await repo.create(
        strategy_kind="bridge",
        strategy_id="bridge:ARBUSDT_autogen_factor",
        symbol="ARB/USDT",
        interval="1h",
        strategy_name="ARB autogen factor",
        exchange="binance",
        market_type="futures",
        params={"start_date": "2025-01-01", "end_date": "2025-06-30"},
        backtest_id="bt-b62d32ac",
        metrics={"sharpe": 1.42, "total_return": 0.18, "trades": 37},
    )
    assert eid.startswith("epi-")
    episode = await repo.get(eid)
    assert episode is not None
    assert episode["strategy_kind"] == "bridge"
    assert episode["strategy_id"] == "bridge:ARBUSDT_autogen_factor"
    assert episode["provenance"] == "RECORDED"
    assert episode["params"]["start_date"] == "2025-01-01"
    assert episode["metrics"]["sharpe"] == 1.42
    assert episode["backtest_id"] == "bt-b62d32ac"


@pytest.mark.asyncio
async def test_metrics_json_passthrough(repo: EpisodeRepository):
    metrics = {
        "sharpe": -0.31,
        "nested": {"sortino": 0.7, "flags": [1, 2, 3]},
        "unicode": "夏普",
    }
    eid = await repo.create(
        strategy_kind="alpha",
        strategy_id="alpha:sig_001",
        symbol="BTC/USDT",
        interval="4h",
        metrics=metrics,
    )
    episode = await repo.get(eid)
    assert episode is not None
    assert episode["metrics"] == metrics


@pytest.mark.asyncio
async def test_list_newest_first_and_pagination(repo: EpisodeRepository):
    first = await repo.create(
        strategy_kind="bridge", strategy_id="bridge:a", symbol="A/USDT", interval="1h"
    )
    second = await repo.create(
        strategy_kind="qd", strategy_id="qd:src48", symbol="B/USDT", interval="4h"
    )
    rows = await repo.list()
    assert [r["id"] for r in rows] == [second, first]
    page = await repo.list(limit=1, offset=1)
    assert [r["id"] for r in page] == [first]


@pytest.mark.asyncio
async def test_list_filters_by_strategy_kind(repo: EpisodeRepository):
    await repo.create(
        strategy_kind="bridge", strategy_id="bridge:a", symbol="A/USDT", interval="1h"
    )
    qd_id = await repo.create(
        strategy_kind="qd", strategy_id="qd:src48", symbol="B/USDT", interval="4h"
    )
    qd_rows = await repo.list(strategy_kind="qd")
    assert [r["id"] for r in qd_rows] == [qd_id]
    assert all(r["strategy_kind"] == "qd" for r in qd_rows)
    assert await repo.list(strategy_kind="preset") == []


@pytest.mark.asyncio
async def test_get_missing_returns_none(repo: EpisodeRepository):
    assert await repo.get("epi-nonexistent") is None

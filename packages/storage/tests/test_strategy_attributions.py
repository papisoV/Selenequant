"""Strategy attribution repository acceptance tests (plan §2.7).

核心契约:字典 seed 五行;UNIQUE (strategy_id, episode_n) 冲突;PUT 改判
created_at 不动;rule_batch 幂等;episode_graph_id NULL 正常;FK 真实生效
(manager 连接 PRAGMA foreign_keys=ON);stats 计数 == 行数。
"""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.strategy_attributions import (
    StrategyAttributionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def attr_env():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield StrategyAttributionRepository(db), db


# 1. dictionary seed -------------------------------------------------------------


@pytest.mark.asyncio
async def test_categories_seed_five_rows(attr_env):
    repo, _ = attr_env
    cats = await repo.list_categories()
    codes = sorted(c["code"] for c in cats)
    assert codes == ["assumption", "data", "execution", "regime_shift", "strategy"]
    assert all(c["content"] for c in cats)
    assert all(c["occurrence_count"] == 1 for c in cats)


# 2. upsert: insert / UNIQUE conflict / PUT overwrite ------------------------------


@pytest.mark.asyncio
async def test_upsert_insert_and_conflict(attr_env):
    repo, _ = attr_env
    row = await repo.upsert("st-a", 1, "strategy", "director", rationale="r1")
    assert row["strategy_id"] == "st-a"
    assert row["episode_n"] == 1
    assert row["category_code"] == "strategy"
    assert row["episode_graph_id"] is None
    with pytest.raises(aiosqlite.IntegrityError, match="UNIQUE"):
        await repo.upsert("st-a", 1, "data", "llm")


@pytest.mark.asyncio
async def test_upsert_overwrite_keeps_created_at(attr_env):
    repo, _ = attr_env
    first = await repo.upsert("st-a", 2, "strategy", "llm")
    second = await repo.upsert("st-a", 2, "regime_shift", "director", overwrite=True)
    assert second["category_code"] == "regime_shift"
    assert second["suggested_by"] == "director"
    assert second["created_at"] == first["created_at"]


@pytest.mark.asyncio
async def test_upsert_rejects_bad_suggested_by(attr_env):
    repo, _ = attr_env
    with pytest.raises(ValueError, match="unknown suggested_by"):
        await repo.upsert("st-a", 1, "data", "oracle")


# 3. FK enforcement (manager connection, per-connection PRAGMA) --------------------


@pytest.mark.asyncio
async def test_fk_bad_category_code_raises(attr_env):
    repo, _ = attr_env
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.upsert("st-a", 1, "not_a_category", "director")


@pytest.mark.asyncio
async def test_fk_bad_episode_graph_id_raises(attr_env):
    repo, _ = attr_env
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.upsert("st-a", 1, "data", "director", episode_graph_id="ep-nope")


@pytest.mark.asyncio
async def test_fk_valid_episode_graph_id_links(attr_env):
    repo, db = attr_env
    await db.execute(
        """
        INSERT INTO research_episodes
            (id, subject_type, subject_id, title, boundary_start,
             boundary_end, recorded_json, occurred_at, created_at)
        VALUES ('ep-real', 'strategy', 'st-a', 't', '2026-01-01',
                '2026-01-02', '{}', '2026-01-02T00:00:00+00:00',
                '2026-01-02T00:00:00+00:00')
        """
    )
    row = await repo.upsert("st-a", 1, "data", "llm", episode_graph_id="ep-real")
    assert row["episode_graph_id"] == "ep-real"


# 4. rule_batch idempotency --------------------------------------------------------


@pytest.mark.asyncio
async def test_rule_batch_idempotent_second_run_zero_new(attr_env):
    repo, db = attr_env
    first = await repo.rule_batch("st-a", [1, 2, 3])
    assert first == [1, 2, 3]
    rows = await db.execute(
        "SELECT COUNT(*) AS n FROM strategy_episode_attributions "
        "WHERE strategy_id='st-a' AND suggested_by='rule'"
    )
    assert rows[0]["n"] == 3
    second = await repo.rule_batch("st-a", [1, 2, 3])
    assert second == []
    rows = await db.execute(
        "SELECT COUNT(*) AS n FROM strategy_episode_attributions "
        "WHERE strategy_id='st-a'"
    )
    assert rows[0]["n"] == 3


@pytest.mark.asyncio
async def test_rule_batch_skips_existing_partial(attr_env):
    repo, _ = attr_env
    await repo.upsert("st-a", 2, "strategy", "director")
    inserted = await repo.rule_batch("st-a", [2, 3, 4])
    assert inserted == [3, 4]
    kept = await repo.for_episode("st-a", 2)
    assert kept["category_code"] == "strategy"
    assert kept["suggested_by"] == "director"


# 5. NULL bridge column + for_episode + stats ---------------------------------------


@pytest.mark.asyncio
async def test_for_episode_none_and_stats_consistency(attr_env):
    repo, db = attr_env
    assert await repo.for_episode("st-a", 99) is None
    await repo.upsert("st-a", 1, "strategy", "llm")
    await repo.rule_batch("st-a", [2, 3])
    await repo.upsert("st-b", 1, "execution", "director")
    stats = await repo.stats()
    assert stats["total"] == 4
    assert sum(stats["counts"].values()) == stats["total"]
    assert stats["counts"] == {"execution": 3, "strategy": 1}
    for detail in stats["episodes"]:
        row = await repo.for_episode(detail["strategy_id"], detail["episode_n"])
        assert row is not None
        assert row["category_code"] == detail["category_code"]
        assert row["episode_graph_id"] is None

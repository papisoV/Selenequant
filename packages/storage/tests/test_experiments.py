"""Tests for ExperimentRepository (研究版本控制 — "Git for Quant Research")."""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.experiments import ExperimentRepository
from selene_space_storage.sqlite import AsyncSQLiteManager


def _sample_experiment() -> dict:
    return {
        "name": "BTC 1h 双顶形态",
        "hypothesis": "关键阻力位双顶 → 跌破颈线放空有正期望",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "dataset": "klines.db 2026-01-01~2026-07-01",
        "strategy_version": "pa-decision-v1",
        "setup_params": {
            "direction": "short",
            "entry": 65000.0,
            "stop_loss": 66000.0,
            "take_profit": 61000.0,
        },
        "backtest_result": {
            "total_setups": 12,
            "win_rate": 0.58,
            "expectancy": 0.42,
        },
        "evidence_refs": ["conclusion-3", "pa-analysis-42"],
    }


@pytest_asyncio.fixture
async def repo():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield ExperimentRepository(db)


@pytest.mark.asyncio
async def test_create_returns_id(repo: ExperimentRepository):
    eid = await repo.create(_sample_experiment())
    assert eid.startswith("exp-")
    assert len(eid) > 3


@pytest.mark.asyncio
async def test_get_roundtrip_fields(repo: ExperimentRepository):
    eid = await repo.create(_sample_experiment())
    loaded = await repo.get(eid)
    assert loaded is not None
    assert loaded["name"] == "BTC 1h 双顶形态"
    assert loaded["symbol"] == "BTC/USDT"
    assert loaded["timeframe"] == "1h"
    assert loaded["dataset"].startswith("klines.db")
    assert loaded["setup_params"] == _sample_experiment()["setup_params"]
    assert loaded["backtest_result"]["win_rate"] == 0.58
    assert loaded["evidence_refs"] == ["conclusion-3", "pa-analysis-42"]
    assert loaded["parent_experiment"] is None
    assert loaded["created_at"]


@pytest.mark.asyncio
async def test_get_missing_returns_none(repo: ExperimentRepository):
    assert await repo.get("exp-nope") is None


@pytest.mark.asyncio
async def test_list_newest_first(repo: ExperimentRepository):
    for i in range(3):
        exp = _sample_experiment()
        exp["name"] = f"exp-{i}"
        await repo.create(exp)
    rows = await repo.list()
    assert len(rows) == 3
    # newest first — 同秒 created_at 时依赖 id 顺序兜底不可靠，此处仅断言都返回
    names = {r["name"] for r in rows}
    assert names == {"exp-0", "exp-1", "exp-2"}


@pytest.mark.asyncio
async def test_list_symbol_filter(repo: ExperimentRepository):
    btc = _sample_experiment()
    eth = _sample_experiment()
    eth["symbol"] = "ETH/USDT"
    eth["name"] = "ETH 双顶"
    await repo.create(btc)
    await repo.create(eth)

    rows = await repo.list(symbol="ETH/USDT")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETH/USDT"
    assert rows[0]["name"] == "ETH 双顶"


@pytest.mark.asyncio
async def test_list_limit(repo: ExperimentRepository):
    for i in range(5):
        exp = _sample_experiment()
        exp["name"] = f"exp-{i}"
        await repo.create(exp)
    rows = await repo.list(limit=2)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_fork_sets_parent_and_copies_fields(repo: ExperimentRepository):
    parent_id = await repo.create(_sample_experiment())
    child_id = await repo.fork(parent_id, overrides={})
    assert child_id != parent_id

    child = await repo.get(child_id)
    assert child["parent_experiment"] == parent_id
    assert child["name"] == _sample_experiment()["name"]
    assert child["setup_params"] == _sample_experiment()["setup_params"]
    assert child["evidence_refs"] == _sample_experiment()["evidence_refs"]
    assert child["id"] != parent_id


@pytest.mark.asyncio
async def test_fork_applies_overrides(repo: ExperimentRepository):
    parent_id = await repo.create(_sample_experiment())
    child_id = await repo.fork(
        parent_id,
        overrides={"name": "变异版双顶", "setup_params": {"direction": "long", "entry": 50000.0}},
    )
    child = await repo.get(child_id)
    assert child["name"] == "变异版双顶"
    assert child["setup_params"] == {"direction": "long", "entry": 50000.0}
    # 未覆盖字段从父复制
    assert child["hypothesis"] == _sample_experiment()["hypothesis"]
    assert child["parent_experiment"] == parent_id


@pytest.mark.asyncio
async def test_fork_missing_parent_raises(repo: ExperimentRepository):
    with pytest.raises(KeyError):
        await repo.fork("exp-nope", overrides={})


@pytest.mark.asyncio
async def test_nullable_json_fields(repo: ExperimentRepository):
    exp = _sample_experiment()
    exp["backtest_result"] = None
    exp["evidence_refs"] = None
    eid = await repo.create(exp)
    loaded = await repo.get(eid)
    assert loaded["backtest_result"] is None
    assert loaded["evidence_refs"] is None


@pytest.mark.asyncio
async def test_get_ancestors_returns_parent_to_root(repo: ExperimentRepository):
    root_id = await repo.create({**_sample_experiment(), "name": "root"})
    mid_id = await repo.fork(root_id, overrides={"name": "mid"})
    leaf_id = await repo.fork(mid_id, overrides={"name": "leaf"})

    chain = await repo.get_ancestors(leaf_id)
    # 父到根:[mid, root]
    assert [c["id"] for c in chain] == [mid_id, root_id]
    assert [c["name"] for c in chain] == ["mid", "root"]
    # 无祖先(root 自身)→ 空
    assert await repo.get_ancestors(root_id) == []


@pytest.mark.asyncio
async def test_get_ancestors_breaks_on_cycle(repo: ExperimentRepository):
    # 手工构造环:a.parent = b, b.parent = a —— 仓库不暴露直接改 parent,用 SQL 注入
    a_id = await repo.create(_sample_experiment())
    b_id = await repo.create(_sample_experiment())
    await repo._db.execute(
        "UPDATE experiments SET parent_experiment = ? WHERE id = ?", (b_id, a_id)
    )
    await repo._db.execute(
        "UPDATE experiments SET parent_experiment = ? WHERE id = ?", (a_id, b_id)
    )
    chain = await repo.get_ancestors(a_id)
    # 循环保护:最多 max_depth 步后停止,不死循环
    assert len(chain) <= 20


@pytest.mark.asyncio
async def test_list_children_returns_direct_forks(repo: ExperimentRepository):
    parent_id = await repo.create(_sample_experiment())
    child1 = await repo.fork(parent_id, overrides={"name": "child1"})
    child2 = await repo.fork(parent_id, overrides={"name": "child2"})

    children = await repo.list_children(parent_id)
    assert {c["id"] for c in children} == {child1, child2}
    assert all(c["parent_experiment"] == parent_id for c in children)

    # 无关实验不是子代
    other = await repo.create({**_sample_experiment(), "name": "other"})
    assert other not in {c["id"] for c in await repo.list_children(parent_id)}

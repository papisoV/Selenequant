"""decision_signals 仓储的 per-skill 聚合(📋3):source_agent 进出维度、
skill_weight_rows 的计数口径、unable/neutral 不进方向分母、NULL 来源不参与。

这些用例锁的是「storage 只出**原料计数**、不下'sample_sufficient'结论」这条边界:
阈值常量住在 backtest-engine,storage 不依赖它,故够不够由装配层判。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.decision_signals import (
    DecisionSignalRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

ENGINE = "decision-signal-v1"


@pytest_asyncio.fixture
async def repo():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield DecisionSignalRepository(db)


def _signal(**overrides) -> dict:
    base = {
        "stock_code": "600519",
        "market": "cn",
        "source_type": "report",
        "source_report_id": "rep-001",
        "trigger_source": "post_market",
        "action": "sell",
        "plan_quality": "ok",
        "status": "active",
    }
    base.update(overrides)
    return base


async def _signal_with_outcome(repo, *, agent, outcome, horizon="5d", report="rep-x"):
    """建一条带 source_agent 的 signal,并给它写一条指定结局的 outcome。

    ``source_agent`` 要**显式写进 outcome**(而不只挂在 signal 上):它是
    outcome 的写时快照列,聚合读的就是它 —— B2 的 service 层
    (``_snapshot_fields``)在真实链路上负责填,这里手工模拟同一行为。
    """
    signal_id, _ = await repo.create_if_absent(
        _signal(source_report_id=report, source_agent=agent, horizon=horizon)
    )
    if outcome == "unable":
        fields = {"eval_status": "unable", "unable_reason": "insufficient_forward_bars"}
    else:
        fields = {
            "eval_status": "completed",
            "outcome": outcome,
            "direction_correct": 1
            if outcome == "hit"
            else (0 if outcome == "miss" else None),
        }
    await repo.upsert_outcome(
        signal_id,
        {"horizon": horizon, "engine_version": ENGINE, "source_agent": agent, **fields},
    )
    return signal_id


# -- source_agent 进查存维度 --------------------------------------------------------


@pytest.mark.asyncio
async def test_same_report_same_action_different_agent_is_two_signals(repo):
    # 同报告同票同 action 同 horizon,但来源 Skill 不同 —— 两个来源的判断,
    # 各自命中率分开累计。若 source_agent 不在查存维度,后写的会收敛掉先写的,
    # per-skill 统计从此对不上账。
    a_id, a_created = await repo.create_if_absent(_signal(source_agent="skill-alpha"))
    b_id, b_created = await repo.create_if_absent(_signal(source_agent="skill-beta"))
    assert a_created and b_created
    assert a_id != b_id


@pytest.mark.asyncio
async def test_same_agent_same_identity_still_dedupes(repo):
    # 加维不能把幂等弄丢:同一 Skill 的同一 identity 仍收敛成一行。
    first_id, first_created = await repo.create_if_absent(
        _signal(source_agent="skill-alpha")
    )
    second_id, second_created = await repo.create_if_absent(
        _signal(source_agent="skill-alpha")
    )
    assert first_created is True
    assert second_created is False
    assert first_id == second_id


@pytest.mark.asyncio
async def test_null_agent_still_dedupes(repo):
    # 两边 source_agent 都 NULL 时判为同一行(NULL 安全 IS 比较)。
    first_id, first_created = await repo.create_if_absent(_signal(source_agent=None))
    second_id, second_created = await repo.create_if_absent(_signal(source_agent=None))
    assert first_created is True
    assert second_created is False
    assert first_id == second_id


# -- skill_weight_rows 计数口径 -----------------------------------------------------


@pytest.mark.asyncio
async def test_skill_weight_rows_split_by_agent_and_horizon(repo):
    await _signal_with_outcome(
        repo, agent="alpha", outcome="hit", horizon="1d", report="r-a1"
    )
    await _signal_with_outcome(
        repo, agent="alpha", outcome="hit", horizon="5d", report="r-a5"
    )
    await _signal_with_outcome(
        repo, agent="beta", outcome="miss", horizon="5d", report="r-b5"
    )
    rows = await repo.skill_weight_rows(engine_version=ENGINE)
    by_key = {(r["skill_id"], r["horizon"]): r for r in rows}
    assert set(by_key) == {("alpha", "1d"), ("alpha", "5d"), ("beta", "5d")}
    assert by_key[("alpha", "5d")]["hit"] == 1
    assert by_key[("beta", "5d")]["miss"] == 1


@pytest.mark.asyncio
async def test_unable_is_counted_pending_not_directional(repo):
    # 诚实拒答单列:unable 不进 hit/miss,也**不**进 evaluated 分母。
    await _signal_with_outcome(repo, agent="alpha", outcome="hit", report="r-h")
    await _signal_with_outcome(repo, agent="alpha", outcome="unable", report="r-u")
    rows = await repo.skill_weight_rows(engine_version=ENGINE)
    row = next(r for r in rows if r["skill_id"] == "alpha")
    assert row["hit"] == 1
    assert row["evaluated"] == 1  # 只算方向性判定
    assert row["unable"] == 1
    assert row["total"] == 2


@pytest.mark.asyncio
async def test_neutral_is_observational_not_directional(repo):
    # neutral(落在中性带)= 已判但既不中也不错 → 记 observational,不入方向分母。
    await _signal_with_outcome(repo, agent="alpha", outcome="hit", report="r-h")
    await _signal_with_outcome(repo, agent="alpha", outcome="neutral", report="r-n")
    rows = await repo.skill_weight_rows(engine_version=ENGINE)
    row = next(r for r in rows if r["skill_id"] == "alpha")
    assert row["evaluated"] == 1
    assert row["observational"] == 1
    assert row["total"] == 2


@pytest.mark.asyncio
async def test_missing_agent_outcomes_are_excluded(repo):
    # 没有 source_agent 的 outcome 无法归因给任何 Skill → 不参与聚合。
    # 把它摊给全体等于凭空发明一个来源。
    await _signal_with_outcome(repo, agent="alpha", outcome="hit", report="r-a")
    await _signal_with_outcome(repo, agent=None, outcome="hit", report="r-none")
    rows = await repo.skill_weight_rows(engine_version=ENGINE)
    assert {r["skill_id"] for r in rows} == {"alpha"}


@pytest.mark.asyncio
async def test_archived_signals_excluded_from_skill_stats(repo):
    # archived = 收进柜子不再计分,与 stats_rows 同口径。
    live = await _signal_with_outcome(
        repo, agent="alpha", outcome="hit", report="r-live"
    )
    dead = await _signal_with_outcome(
        repo, agent="alpha", outcome="miss", report="r-dead"
    )
    await repo.set_status(dead, "archived")
    rows = await repo.skill_weight_rows(engine_version=ENGINE)
    row = next(r for r in rows if r["skill_id"] == "alpha")
    assert row["hit"] == 1
    assert row["miss"] == 0


@pytest.mark.asyncio
async def test_other_engine_version_excluded(repo):
    await _signal_with_outcome(repo, agent="alpha", outcome="hit", report="r-v1")
    signal_id, _ = await repo.create_if_absent(
        _signal(source_report_id="r-v2", source_agent="alpha")
    )
    await repo.upsert_outcome(
        signal_id,
        {
            "horizon": "5d",
            "engine_version": "decision-signal-v2",
            "eval_status": "unable",
        },
    )
    rows = await repo.skill_weight_rows(engine_version=ENGINE)
    assert {r["horizon"] for r in rows if r["skill_id"] == "alpha"} == {"5d"}
    assert next(r for r in rows if r["skill_id"] == "alpha")["unable"] == 0


@pytest.mark.asyncio
async def test_skill_ids_filter_and_empty_statuses(repo):
    await _signal_with_outcome(repo, agent="alpha", outcome="hit", report="r-a")
    await _signal_with_outcome(repo, agent="beta", outcome="hit", report="r-b")
    only_alpha = await repo.skill_weight_rows(
        engine_version=ENGINE, skill_ids=["alpha"]
    )
    assert {r["skill_id"] for r in only_alpha} == {"alpha"}
    assert await repo.skill_weight_rows(engine_version=ENGINE, statuses=()) == []


@pytest.mark.asyncio
async def test_horizon_filter(repo):
    await _signal_with_outcome(
        repo, agent="alpha", outcome="hit", horizon="1d", report="r-1"
    )
    await _signal_with_outcome(
        repo, agent="alpha", outcome="hit", horizon="5d", report="r-5"
    )
    rows = await repo.skill_weight_rows(engine_version=ENGINE, horizons=["5d"])
    assert {r["horizon"] for r in rows} == {"5d"}

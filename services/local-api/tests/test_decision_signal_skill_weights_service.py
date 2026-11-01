"""skill_weights 装配层(📋3):三层汇合、阈值在此判定、中性因子、口径一致。

锁的是**装配**行为:storage 出计数 → 本层判 sample_sufficient → backtest-engine
的纯函数出因子。公式本身在 ``test_skill_weight.py``;仓储计数口径在
``test_decision_signals_skill_weights.py``。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.core.decision_signal_outcome import DecisionSignalOutcomeService
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.decision_signals import (
    DecisionSignalRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

ENGINE = "decision-signal-v1"


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as mgr:
        yield mgr


def _service(db) -> DecisionSignalOutcomeService:
    # skill_weights 不取行情,故 fetch_daily 永不被调用 —— 给一个会炸的哨兵,
    # 一旦被调用即暴露"这条路不该读价"的契约违反。
    def _never(_symbol):
        raise AssertionError("skill_weights must not fetch daily bars")

    return DecisionSignalOutcomeService(db, fetch_daily=_never)


async def _seed(db, *, agent, outcome, horizon="5d", report="rep-x", count=1):
    repo = DecisionSignalRepository(db)
    for i in range(count):
        signal_id, _ = await repo.create_if_absent(
            {
                "stock_code": "600519",
                "market": "cn",
                "source_type": "report",
                "source_report_id": f"{report}-{i}",
                "source_agent": agent,
                "trigger_source": "post_market",
                "action": "sell",
                "horizon": horizon,
                "plan_quality": "ok",
                "status": "active",
            }
        )
        if outcome == "unable":
            fields = {
                "eval_status": "unable",
                "unable_reason": "insufficient_forward_bars",
            }
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
            {
                "horizon": horizon,
                "engine_version": ENGINE,
                "source_agent": agent,
                **fields,
            },
        )


@pytest.mark.asyncio
async def test_empty_db_returns_no_skills(db):
    out = await _service(db).skill_weights()
    assert out["skills"] == []
    assert out["engine_version"] == ENGINE
    assert out["minimum_evaluated_sample_size"] == 30


@pytest.mark.asyncio
async def test_requested_skill_with_no_data_gets_neutral_entry(db):
    # 点名一个库中没有的 Skill:回一条中性因子记录(前端要画一行),不是 404。
    out = await _service(db).skill_weights(skill_ids=["ghost"])
    assert [s["skill_id"] for s in out["skills"]] == ["ghost"]
    assert out["skills"][0]["factor"] == 1.0
    assert out["skills"][0]["buckets"] == []


@pytest.mark.asyncio
async def test_scores_are_assembled_from_storage_counts(db):
    await _seed(db, agent="alpha", outcome="hit", count=40)
    out = await _service(db).skill_weights()
    skill = next(s for s in out["skills"] if s["skill_id"] == "alpha")
    assert skill["factor"] > 1.0
    assert skill["buckets"][0]["hit"] == 40
    assert skill["buckets"][0]["sample_sufficient"] is True


@pytest.mark.asyncio
async def test_below_threshold_is_flagged_and_stays_neutral(db):
    # 25 个命中 < 30 → 样本不足:如实标出来,因子保持 1.0(不提前给权重)。
    await _seed(db, agent="alpha", outcome="hit", count=25)
    out = await _service(db).skill_weights()
    skill = next(s for s in out["skills"] if s["skill_id"] == "alpha")
    assert skill["buckets"][0]["sample_sufficient"] is False
    assert skill["factor"] == 1.0


@pytest.mark.asyncio
async def test_several_skills_are_each_scored(db):
    await _seed(db, agent="alpha", outcome="hit", count=40, report="a")
    await _seed(db, agent="beta", outcome="miss", count=40, report="b")
    out = await _service(db).skill_weights()
    by_id = {s["skill_id"]: s for s in out["skills"]}
    assert by_id["alpha"]["factor"] > 1.0
    assert by_id["beta"]["factor"] < 1.0


@pytest.mark.asyncio
async def test_skill_ids_filter_narrows_result_but_includes_requested(db):
    await _seed(db, agent="alpha", outcome="hit", count=40, report="a")
    await _seed(db, agent="beta", outcome="hit", count=40, report="b")
    out = await _service(db).skill_weights(skill_ids=["alpha"])
    assert [s["skill_id"] for s in out["skills"]] == ["alpha"]


@pytest.mark.asyncio
async def test_horizon_filter_narrows_buckets(db):
    await _seed(db, agent="alpha", outcome="hit", horizon="1d", count=40, report="d1")
    await _seed(db, agent="alpha", outcome="hit", horizon="5d", count=40, report="d5")
    out = await _service(db).skill_weights(horizons=["5d"])
    skill = next(s for s in out["skills"] if s["skill_id"] == "alpha")
    assert [b["horizon"] for b in skill["buckets"]] == ["5d"]


@pytest.mark.asyncio
async def test_weights_never_fetch_market_data(db):
    # 契约:权重是纯统计,不碰行情。fetch_daily 是哨兵,一旦被调即 AssertionError。
    await _seed(db, agent="alpha", outcome="hit", count=40)
    out = await _service(db).skill_weights()
    assert out["skills"]


@pytest.mark.asyncio
async def test_unable_only_skill_is_not_scored(db):
    await _seed(db, agent="alpha", outcome="unable", count=40)
    out = await _service(db).skill_weights()
    skill = next(s for s in out["skills"] if s["skill_id"] == "alpha")
    assert skill["buckets"][0]["unable"] == 40
    assert skill["buckets"][0]["evaluated"] == 0
    assert skill["factor"] == 1.0


@pytest.mark.asyncio
async def test_engine_version_override_filters(db):
    await _seed(db, agent="alpha", outcome="hit", count=40)
    out = await _service(db).skill_weights(engine_version="decision-signal-v9")
    assert out["skills"] == []

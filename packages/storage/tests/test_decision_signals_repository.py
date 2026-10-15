"""decision_signals 仓储:identity 查存幂等、horizon 隔离、outcome upsert 语义、
诚实拒答不变量、archived 出局统计、候选扫描的"逐 horizon"口径。"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.decision_signals import (
    DEFAULT_STATS_STATUSES,
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
        "stock_name": "贵州茅台",
        "market": "cn",
        "source_type": "report",
        "source_report_id": "rep-001",
        "trace_id": "tr-001",
        "trigger_source": "post_market",
        "action": "sell",
        "plan_quality": "ok",
        "status": "active",
    }
    base.update(overrides)
    return base


async def _signal_id(repo, **overrides) -> int:
    signal_id, _created = await repo.create_if_absent(_signal(**overrides))
    return signal_id


# -- identity / 幂等 --------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_returns_new_row_then_replays(repo):
    first_id, first_created = await repo.create_if_absent(_signal())
    second_id, second_created = await repo.create_if_absent(_signal())
    assert first_created is True
    assert second_created is False
    assert first_id == second_id


@pytest.mark.asyncio
async def test_replay_does_not_mutate_the_stored_row(repo):
    # 重放是正常路径,不是"更新" —— 落库后 signal 的字段不该被后来的同 identity
    # 写入悄悄改掉(改了就把历史建议篡改了)。
    signal_id, _ = await repo.create_if_absent(
        _signal(source_report_id="rep-mut", confidence=0.9)
    )
    await repo.create_if_absent(_signal(source_report_id="rep-mut", confidence=0.1))
    row = await repo.get(signal_id)
    assert row["confidence"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_same_report_different_horizon_is_two_signals(repo):
    # 同一条报告对同一只票给出 1d 与 10d 两档建议,都是合法行 —— 这正是
    # 不该用数据库唯一键、且 horizon 必须进查存维度的原因。
    short_id, short_created = await repo.create_if_absent(_signal(horizon="1d"))
    long_id, long_created = await repo.create_if_absent(_signal(horizon="10d"))
    assert short_created and long_created
    assert short_id != long_id


@pytest.mark.asyncio
async def test_same_report_same_horizon_different_action_is_two_signals(repo):
    # action 也在查存维度里:同报告同票同 horizon,但一个 sell 一个 buy,
    # 是两条不同的建议(DSA ``_find_existing_in_session`` 八条件含 action)。
    sell_id, sell_created = await repo.create_if_absent(_signal(action="sell"))
    buy_id, buy_created = await repo.create_if_absent(_signal(action="buy"))
    assert sell_created and buy_created
    assert sell_id != buy_id


@pytest.mark.asyncio
async def test_unanchored_signals_are_never_deduped(repo):
    # 手工信号既无 source_report_id 又无 trace_id → 没有可比对的锚,每次都是新行。
    # (DSA ``_find_existing_in_session`` 在该分支直接 return None。)
    manual = {"source_type": "manual", "source_report_id": None, "trace_id": None}
    first_id, first_created = await repo.create_if_absent(_signal(**manual))
    second_id, second_created = await repo.create_if_absent(_signal(**manual))
    assert first_created is True
    assert second_created is True
    assert first_id != second_id


@pytest.mark.asyncio
async def test_trace_id_anchors_when_report_id_is_absent(repo):
    # trace_id 是 source_report_id 缺席时的回退锚(同 DSA 的 elif 分支)。
    traced = {"source_report_id": None, "trace_id": "tr-shared"}
    first_id, first_created = await repo.create_if_absent(_signal(**traced))
    second_id, second_created = await repo.create_if_absent(_signal(**traced))
    assert first_created is True
    assert second_created is False
    assert first_id == second_id


@pytest.mark.asyncio
async def test_null_market_phase_still_dedupes(repo):
    # 可空维度(market_phase / decision_profile)两边都是 NULL 时必须判为同一行,
    # 否则每次写入都会重复落库。依赖 SQLite 的 NULL 安全 `IS` 比较。
    identical = {"source_report_id": "rep-null-ish", "market_phase": None}
    first_id, first_created = await repo.create_if_absent(_signal(**identical))
    second_id, second_created = await repo.create_if_absent(_signal(**identical))
    assert first_created is True
    assert second_created is False
    assert first_id == second_id


@pytest.mark.asyncio
async def test_different_market_phase_is_a_distinct_signal(repo):
    a_id, a_created = await repo.create_if_absent(_signal(market_phase="premarket"))
    b_id, b_created = await repo.create_if_absent(_signal(market_phase="postmarket"))
    assert a_created and b_created
    assert a_id != b_id


@pytest.mark.asyncio
async def test_different_stock_is_a_distinct_signal(repo):
    a_id, a_created = await repo.create_if_absent(_signal(stock_code="600519"))
    b_id, b_created = await repo.create_if_absent(_signal(stock_code="000001"))
    assert a_created and b_created
    assert a_id != b_id


@pytest.mark.asyncio
async def test_json_columns_accept_structured_values(repo):
    signal_id, _ = await repo.create_if_absent(
        _signal(evidence_json={"k": [1, 2]}, metadata_json={"v": 2})
    )
    row = await repo.get(signal_id)
    assert '"k"' in row["evidence_json"]
    assert '"v"' in row["metadata_json"]


# -- status ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_status_hits_and_misses(repo):
    signal_id = await _signal_id(repo)
    assert await repo.set_status(signal_id, "archived") is True
    assert (await repo.get(signal_id))["status"] == "archived"
    assert await repo.set_status(signal_id + 9999, "archived") is False


# -- outcome upsert --------------------------------------------------------------


@pytest.mark.asyncio
async def test_outcome_upsert_is_single_row_with_first_created_at(repo):
    signal_id = await _signal_id(repo)
    await repo.upsert_outcome(
        signal_id,
        {
            "horizon": "5d",
            "engine_version": ENGINE,
            "eval_status": "unable",
            "unable_reason": "insufficient_forward_bars",
        },
    )
    before = await repo.get_outcome(signal_id, "5d", ENGINE)
    await repo.upsert_outcome(
        signal_id,
        {
            "horizon": "5d",
            "engine_version": ENGINE,
            "eval_status": "completed",
            "outcome": "hit",
            "direction_correct": 1,
            "unable_reason": None,  # 重评必须显式清掉旧拒答因由(否则库里留脏值)
        },
    )
    after = await repo.get_outcome(signal_id, "5d", ENGINE)
    assert await repo.count_outcomes(signal_id) == 1
    assert after["eval_status"] == "completed"
    assert after["outcome"] == "hit"
    assert after["created_at"] == before["created_at"]  # 首插值不动
    assert after["unable_reason"] is None


@pytest.mark.asyncio
async def test_horizons_and_engine_versions_are_independent(repo):
    signal_id = await _signal_id(repo)
    for horizon in ("1d", "5d"):
        await repo.upsert_outcome(
            signal_id,
            {"horizon": horizon, "engine_version": ENGINE, "eval_status": "unable"},
        )
    await repo.upsert_outcome(
        signal_id,
        {"horizon": "5d", "engine_version": "decision-signal-v2", "eval_status": "unable"},
    )
    assert await repo.count_outcomes(signal_id) == 3


@pytest.mark.asyncio
async def test_unable_outcome_carries_no_verdict(repo):
    # 诚实拒答:unable 时绝不能顺手填个 verdict。CHECK 兜底,仓储不该绕过。
    signal_id = await _signal_id(repo)
    with pytest.raises(Exception, match="CHECK"):
        await repo.upsert_outcome(
            signal_id,
            {
                "horizon": "5d",
                "engine_version": ENGINE,
                "eval_status": "unable",
                "outcome": "miss",
            },
        )


# -- stats -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_rows_exclude_archived_and_unable(repo):
    hit_id = await _signal_id(repo, source_report_id="rep-hit")
    archived_id = await _signal_id(repo, source_report_id="rep-arch")
    unable_id = await _signal_id(repo, source_report_id="rep-unable")
    await repo.upsert_outcome(
        hit_id,
        {
            "horizon": "5d",
            "engine_version": ENGINE,
            "eval_status": "completed",
            "outcome": "hit",
            "direction_correct": 1,
        },
    )
    await repo.set_status(archived_id, "archived")
    await repo.upsert_outcome(
        archived_id,
        {
            "horizon": "5d",
            "engine_version": ENGINE,
            "eval_status": "completed",
            "outcome": "miss",
            "direction_correct": 0,
        },
    )
    await repo.upsert_outcome(
        unable_id,
        {"horizon": "5d", "engine_version": ENGINE, "eval_status": "unable"},
    )
    rows = await repo.stats_rows(engine_version=ENGINE)
    assert [r["outcome"] for r in rows] == ["hit"]


@pytest.mark.asyncio
async def test_archived_is_not_in_default_stats_statuses(repo):
    assert "archived" not in DEFAULT_STATS_STATUSES


# -- candidate scan --------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_scan_returns_all_rows_in_requested_statuses(repo):
    # 仓储只负责"按 status 取一页原料";某个 horizon 是否已判、该不该重评,
    # 是服务层 actionability 的判定(同 DSA)。这里锁住仓储的边界。
    a_id = await _signal_id(repo, source_report_id="rep-c-a")
    b_id = await _signal_id(repo, source_report_id="rep-c-b")
    archived_id = await _signal_id(repo, source_report_id="rep-c-arch")
    await repo.set_status(archived_id, "archived")
    rows = await repo.list_candidate_signals(statuses=("active",), limit=50)
    assert {r["id"] for r in rows} == {a_id, b_id}


@pytest.mark.asyncio
async def test_candidate_scan_respects_limit_and_empty_statuses(repo):
    for n in range(3):
        await _signal_id(repo, source_report_id=f"rep-lim-{n}")
    assert len(await repo.list_candidate_signals(statuses=("active",), limit=2)) == 2
    assert await repo.list_candidate_signals(statuses=(), limit=50) == []


@pytest.mark.asyncio
async def test_outcomes_for_signals_batches_by_engine(repo):
    a = await _signal_id(repo, source_report_id="rep-b-a")
    b = await _signal_id(repo, source_report_id="rep-b-b")
    await repo.upsert_outcome(
        a, {"horizon": "5d", "engine_version": ENGINE, "eval_status": "unable"}
    )
    await repo.upsert_outcome(
        b, {"horizon": "5d", "engine_version": "decision-signal-v2", "eval_status": "unable"}
    )
    rows = await repo.list_outcomes_for_signals([a, b], ENGINE)
    assert [r["signal_id"] for r in rows] == [a]
    assert await repo.list_outcomes_for_signals([], ENGINE) == []


# -- listing ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_signals_filters(repo):
    await _signal_id(repo, stock_code="600519", action="sell")
    await _signal_id(repo, stock_code="000001", action="buy")
    assert len(await repo.list_signals(market="cn")) == 2
    assert len(await repo.list_signals(action="buy")) == 1
    assert len(await repo.list_signals(stock_code="600519")) == 1
    assert len(await repo.list_signals(status="expired")) == 0


@pytest.mark.asyncio
async def test_list_outcomes_are_per_signal(repo):
    a = await _signal_id(repo, source_report_id="rep-a2")
    b = await _signal_id(repo, source_report_id="rep-b2")
    await repo.upsert_outcome(
        a, {"horizon": "1d", "engine_version": ENGINE, "eval_status": "unable"}
    )
    assert len(await repo.list_outcomes(a)) == 1
    assert await repo.list_outcomes(b) == []

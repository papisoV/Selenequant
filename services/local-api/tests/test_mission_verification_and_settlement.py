"""mission_verifier 的门槛判定与 mission_settlement 的结案产物。

这些是 2026-10-25 "真正跑通五步环"的三个硬约束,每条都用断言钉住:

1. **没有实验就没有证据** —— 门槛不过一律 ``untested``,Sharpe 再高也不分级;
2. **结案必须留下决策** —— 有归属问题时写 ``research_decisions``,并派生
   下一个问题、设复访时点;
3. **没有归属问题时不静默** —— 决策跳过要写在 ``warnings`` 里。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.core.mission_settlement import settle_mission
from app.core.mission_verifier import (
    MIN_TRADES,
    VerificationOutcome,
    evaluate_gates,
    verify_mission,
)

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.mission_thesis import MissionThesisRepository
from selene_space_storage.repositories.research_graph import (
    ResearchQuestionRepository,
)
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


def _metrics(**over: object) -> dict:
    base: dict[str, object] = {
        "trades": 200,
        "sharpe": 1.5,
        "permutation_p": 0.02,
        "ci_lower": 0.3,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- evaluate_gates


def test_all_gates_pass_then_grade_by_sharpe() -> None:
    gates, signal = evaluate_gates(_metrics(sharpe=1.5))
    assert signal == "confirming"
    assert [g.name for g in gates] == [
        "sample_size",
        "permutation",
        "sharpe_ci",
        "sharpe_level",
    ]
    assert all(g.passed for g in gates)


def test_thin_sample_is_untested_however_good_the_sharpe() -> None:
    gates, signal = evaluate_gates(_metrics(trades=MIN_TRADES - 1, sharpe=9.9))
    assert signal == "untested"
    assert gates[-1].name == "sample_size"
    assert not gates[-1].passed


def test_insignificant_permutation_is_untested() -> None:
    gates, signal = evaluate_gates(_metrics(permutation_p=0.42, sharpe=9.9))
    assert signal == "untested"
    assert gates[-1].name == "permutation"


def test_missing_permutation_result_is_untested_not_guessed() -> None:
    gates, signal = evaluate_gates(_metrics(permutation_p=None))
    assert signal == "untested"
    assert "无法计算" in gates[-1].reason


def test_ci_crossing_zero_is_untested() -> None:
    gates, signal = evaluate_gates(_metrics(ci_lower=0.0, sharpe=9.9))
    assert signal == "untested"
    assert gates[-1].name == "sharpe_ci"


def test_strongly_negative_sharpe_invalidates() -> None:
    _, signal = evaluate_gates(_metrics(sharpe=-1.8))
    assert signal == "invalidating"


def test_real_backtest_shape_on_btc_1h_is_gated_not_graded() -> None:
    """真实回测(2026-10-25 实测 BTC/USDT 1h,18329 根)的形状照原样过门槛。

    Sharpe 为负且置换 p=0.576 —— 不显著。门槛必须先判"untested",不能
    因为成交笔数够多就顺手分级。
    """
    _, signal = evaluate_gates(
        {
            "trades": 197,
            "sharpe": -0.6142,
            "permutation_p": 0.576,
            "ci_lower": -1.9914,
            "ci_upper": 0.6741,
        }
    )
    assert signal == "untested"


# -------------------------------------------------------------- verify_mission


async def _mission(
    db, *, question: bool = True, strategy_id: str | None = "rsi_ma_delta"
) -> tuple[str, str | None]:
    question_id = None
    if question:
        question_id = await ResearchQuestionRepository(db).create(
            title="BTC 1h 上 rsi_ma_delta 是否真有边缘?",
            anchor={"symbols": ["BTC/USDT"], "intervals": ["1h"]},
        )
    context: dict = {}
    if question_id:
        context["question_id"] = question_id
    if strategy_id:
        context["strategy_id"] = strategy_id
    mission_id = await ResearchMissionRepository(db).create(
        title="验证 1h 边缘",
        question="BTC 1h 上 rsi_ma_delta 是否真有边缘?",
        subject_type="market",
        subject_id="BTC/USDT",
        created_by="user",
        context=context,
    )
    return mission_id, question_id


async def _run_ok(strategy_id, symbol, interval, parameters=None, **kwargs):
    return {
        "ok": True,
        "bars": 2880,
        "start": "2025-01-01",
        "end": "2025-04-01",
        "trades": 64,
        "total_return": 0.12,
        "sharpe": 1.31,
        "max_drawdown": -0.08,
        "win_rate": 0.55,
        "profit_factor": 1.4,
        "permutation_p": 0.03,
        "ci_lower": 0.21,
        "ci_upper": 2.1,
        "prob_positive": 0.98,
    }


@pytest.mark.asyncio
async def test_verify_writes_experiment_and_evidence(db) -> None:
    mission_id, _ = await _mission(db)
    outcome = await verify_mission(db, mission_id, run_fn=_run_ok)

    assert outcome.ran is True
    assert outcome.signal == "confirming"
    assert outcome.experiment_id
    assert outcome.evidence_id
    assert outcome.pillar_id  # 自动开的那根可证伪支柱

    evidence = await MissionThesisRepository(db).list_evidence(mission_id)
    assert len(evidence) == 1
    assert evidence[0]["signal"] == "confirming"
    assert evidence[0]["source_type"] == "backtest"
    assert evidence[0]["source_ref"] == f"experiment:{outcome.experiment_id}"


@pytest.mark.asyncio
async def test_verify_without_strategy_target_does_not_run(db) -> None:
    mission_id, _ = await _mission(db, question=False, strategy_id=None)
    outcome = await verify_mission(db, mission_id, run_fn=_run_ok)

    assert outcome.ran is False
    assert outcome.signal == "untested"
    assert "策略" in outcome.reason
    assert await MissionThesisRepository(db).list_evidence(mission_id) == []


@pytest.mark.asyncio
async def test_verify_uses_subject_id_when_mission_is_about_a_strategy(db) -> None:
    """策略退化类问题:subject_id → strategies 行 → template:<id> → 回测模板。

    ``strategies.id`` 是库内主键,不是模板名;模板身份的权威来源是
    ``config_json.source`` 的 ``template:`` 前缀。直接用 subject_id 当模板
    会得到 "unknown preset strategy"。
    """
    await db.execute(
        "INSERT INTO strategies (id, name, type, config_json, created_at, updated_at) "
        "VALUES ('strat-bb-v3', 'bb_v3', 'oscillator', ?, "
        "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        (
            '{"id": "strat-bb-v3", "name": "bb_v3", "type": "oscillator", '
            '"symbols": [], "interval": "1h", "source": "template:bb_v3"}',
        ),
    )
    mission_id = await ResearchMissionRepository(db).create(
        title="bb_v3 是否已退化",
        question="bb_v3 在 BTC 1h 上是否已退化?",
        subject_type="strategy",
        subject_id="strat-bb-v3",
        created_by="user",
        context={"symbols": ["BTC/USDT"], "intervals": ["1h"]},
    )

    from app.core.mission_verifier import resolve_verification_target

    mission = await ResearchMissionRepository(db).get(mission_id)
    assert mission is not None
    target = await resolve_verification_target(db, mission, {}, [])
    assert target["strategy_id"] == "bb_v3"
    assert target["symbol"] == "BTC/USDT"
    assert target["interval"] == "1h"


@pytest.mark.asyncio
async def test_strategy_subject_without_template_identity_does_not_guess(db) -> None:
    """解不出模板就不跑 —— 不许拿别的策略的结果当证据。"""
    await db.execute(
        "INSERT INTO strategies (id, name, type, config_json, created_at, updated_at) "
        "VALUES ('strat-handmade', '手写策略', 'custom', ?, "
        "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        (
            '{"id": "strat-handmade", "name": "手写策略", "type": "custom", '
            '"symbols": [], "interval": "1h"}',
        ),
    )
    mission_id = await ResearchMissionRepository(db).create(
        title="手写策略是否有效",
        question="手写策略在 BTC 1h 上是否有效?",
        subject_type="strategy",
        subject_id="strat-handmade",
        created_by="user",
        context={"symbols": ["BTC/USDT"]},
    )
    outcome = await verify_mission(db, mission_id, run_fn=_run_ok)
    assert outcome.ran is False
    assert "策略" in outcome.reason


@pytest.mark.asyncio
async def test_verify_marks_experiment_failed_when_backtest_errors(db) -> None:
    mission_id, _ = await _mission(db)

    async def _boom(*args, **kwargs):
        return {"ok": False, "error": "no data"}

    outcome = await verify_mission(db, mission_id, run_fn=_boom)
    assert outcome.ran is False
    assert outcome.experiment_id  # 尝试过就要留痕
    assert "no data" in outcome.reason


# ------------------------------------------------------------ settle_mission


@pytest.mark.asyncio
async def test_settle_records_decision_child_question_and_revisit(db) -> None:
    mission_id, question_id = await _mission(db)
    assert question_id is not None
    mission = await ResearchMissionRepository(db).get(mission_id)
    assert mission is not None

    result = await settle_mission(
        db,
        mission,
        {"verdict": "SUPPORTED", "next_question": "那么 4h 呢?"},
    )

    assert result.ok
    assert result.decision_id
    assert result.child_question_id
    assert result.revisit_at
    # 子问题没声明 anchor → 继承父问题,但必须**明说**是继承来的
    assert len(result.warnings) == 1
    assert "继承父问题" in result.warnings[0]

    rows = await db.execute(
        "SELECT question_id, verdict, decided_by, window_start, window_end "
        "FROM research_decisions WHERE id = ?",
        (result.decision_id,),
    )
    assert rows[0]["question_id"] == question_id
    assert rows[0]["verdict"] == "SUPPORTED"
    # schema 只有这一个合法值 —— AI 无法冒充 Director
    assert rows[0]["decided_by"] == "director"
    assert rows[0]["window_start"] and rows[0]["window_end"]

    child = await ResearchQuestionRepository(db).get(result.child_question_id)
    assert child is not None
    assert child["parent_question_id"] == question_id
    assert child["created_by"] == "selene"
    assert child["anchor"]  # 继承父问题锚

    parent = await ResearchQuestionRepository(db).get(question_id)
    assert parent is not None
    assert parent["next_revisit_at"] == result.revisit_at


@pytest.mark.asyncio
async def test_settle_without_question_warns_instead_of_silently_dropping(db) -> None:
    mission_id, _ = await _mission(db, question=False)
    mission = await ResearchMissionRepository(db).get(mission_id)
    assert mission is not None

    result = await settle_mission(db, mission, {"verdict": "PARKED"})

    assert result.decision_id is None
    assert result.child_question_id is None
    assert any("question_id" in w for w in result.warnings)
    decisions = await db.execute("SELECT COUNT(*) AS n FROM research_decisions")
    assert decisions[0]["n"] == 0


@pytest.mark.asyncio
async def test_settle_derives_next_question_from_pillar_proof_point(db) -> None:
    mission_id, question_id = await _mission(db)
    assert question_id is not None
    await MissionThesisRepository(db).add_pillar(
        mission_id,
        "费率回落先于价格见顶",
        "core",
        next_proof_point="验证 4h 上同一策略的置换检验",
    )
    mission = await ResearchMissionRepository(db).get(mission_id)
    assert mission is not None

    result = await settle_mission(db, mission, {"verdict": "SUPPORTED"})

    assert result.child_question_id
    child = await ResearchQuestionRepository(db).get(result.child_question_id)
    assert child is not None
    assert child["title"] == "验证 4h 上同一策略的置换检验"


@pytest.mark.asyncio
async def test_settle_does_not_turn_a_bare_date_into_a_question(db) -> None:
    """老数据把 next_proof_point 当日期存 —— 别把它变成一条无意义的问题。"""
    mission_id, _ = await _mission(db)
    await MissionThesisRepository(db).add_pillar(
        mission_id, "某支柱", "core", next_proof_point="2026-09-10"
    )
    mission = await ResearchMissionRepository(db).get(mission_id)
    assert mission is not None

    result = await settle_mission(db, mission, {"verdict": "SUPPORTED"})
    assert result.child_question_id is None


@pytest.mark.asyncio
async def test_settle_accepts_outcome_window_override(db) -> None:
    mission_id, _ = await _mission(db)
    mission = await ResearchMissionRepository(db).get(mission_id)
    assert mission is not None

    result = await settle_mission(
        db,
        mission,
        {
            "verdict": "SUPPORTED",
            "window_start": "2026-01-01T00:00:00+00:00",
            "window_end": "2026-02-01T00:00:00+00:00",
        },
    )
    assert result.window_start == "2026-01-01T00:00:00+00:00"
    assert result.window_end == "2026-02-01T00:00:00+00:00"
    assert result.revisit_at is not None
    assert result.revisit_at.startswith("2026-02-08")


@pytest.mark.asyncio
async def test_settle_finds_question_through_addresses_edge(db) -> None:
    """手建 mission 只有 addresses 边 —— 结算也必须认。"""
    from selene_space_storage.repositories.research_graph import (
        ResearchEdgeRepository,
    )

    question_id = await ResearchQuestionRepository(db).create(
        title="手建问题", anchor={"symbols": ["ETH/USDT"]}
    )
    mission_id = await ResearchMissionRepository(db).create(
        title="手建 mission",
        question="手建问题?",
        subject_type="market",
        subject_id="ETH/USDT",
        created_by="user",
        # 故意不写 context.question_id
    )
    await ResearchEdgeRepository(db).add_fact_edge(
        ("mission", mission_id), ("question", question_id), "addresses", actor="user"
    )

    mission = await ResearchMissionRepository(db).get(mission_id)
    assert mission is not None
    result = await settle_mission(db, mission, {"verdict": "SUPPORTED"})

    assert result.ok
    rows = await db.execute(
        "SELECT question_id FROM research_decisions WHERE id = ?",
        (result.decision_id,),
    )
    assert rows[0]["question_id"] == question_id


def test_verification_outcome_defaults_to_untested() -> None:
    outcome = VerificationOutcome(mission_id="mis-x", ran=False)
    assert outcome.signal == "untested"

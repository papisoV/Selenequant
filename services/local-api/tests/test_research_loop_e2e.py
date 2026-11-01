"""The full five-step loop, asserted end to end (no network, real machinery).

The probe that produced this test ran the production path against real BTC 1h
klines, a real backtest and the configured LLM. This test pins the *shape* of
that run: material at the declared interval → code-owned verdict → evidence →
needs_input → decision + child question + revisit.

The backtest and LLM are injected (the real ones take ~40s and cost a call);
everything between them is the production code path.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.core.mission_settlement import settle_mission
from app.core.mission_verifier import VerificationOutcome
from app.core.mission_worker import mission_worker_round
from app.services.market_material import MarketMaterial

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import (
    ResearchEdgeRepository,
    ResearchQuestionRepository,
)
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


# The exact numbers the real run produced on 2026-10-25: p fails the gate, so
# no amount of narrative may turn this into "supports".
_BTC_1H_METRICS = {
    "ok": True,
    "bars": 18329,
    "start": "2024-09-19",
    "end": "2026-10-23",
    "trades": 197,
    "sharpe": -0.6142,
    "total_return": -0.32296169,
    "max_drawdown": -0.52544261,
    "permutation_p": 0.576,
    "ci_lower": -1.9914,
    "ci_upper": 0.6741,
    "prob_positive": 0.174,
    "profit_factor": 0.8542,
    "win_rate": 0.3858,
    "strategy_id": "rsi_ma_delta",
    "symbol": "BTC/USDT",
    "interval": "1h",
}


async def _open_investigation(db, *, interval: str = "1h") -> tuple[str, str]:
    qrepo = ResearchQuestionRepository(db)
    question_id = await qrepo.create(
        title="BTC 1h 上 rsi_ma_delta 是否有可持续的统计边缘?",
        anchor={"symbols": ["BTC/USDT"], "intervals": [interval]},
    )
    mrepo = ResearchMissionRepository(db)
    mission_id = await mrepo.create(
        title="验证 BTC 1h RSI 偏离均线边缘",
        question="在 BTC/USDT 1h 上,RSI 偏离均线的信号是否具有可持续的统计边缘?",
        subject_type="market",
        subject_id="BTC/USDT",
        created_by="user",
        context={
            "question_id": question_id,
            "strategy_id": "rsi_ma_delta",
            "interval": interval,
        },
    )
    return question_id, mission_id


@pytest.mark.asyncio
async def test_full_loop_from_investigation_to_loop_seed(db) -> None:
    question_id, mission_id = await _open_investigation(db)
    mrepo = ResearchMissionRepository(db)
    assert (await mrepo.get(mission_id))["phase"] == "investigation"

    seen: dict[str, object] = {}

    async def fake_material(_db, anchor, _key):
        seen["material_interval"] = anchor.get("intervals")
        return MarketMaterial(
            state="ok",
            text="BTC/USDT 1h 18329 根,2024-09-19 ~ 2026-10-23",
            symbol="BTC/USDT",
            interval="1h",
            funding_available=True,
            reason="已注入 BTC/USDT 1h 行情快照",
        )

    async def fake_verify(_db, mission_id, *, anchor):
        # The verifier adjudicates; the model only explains. This is the
        # production contract, not a shortcut.
        return VerificationOutcome(
            mission_id=mission_id,
            ran=True,
            signal="untested",
            reason="p=0.576(上限 0.1)",
            evidence_id=None,
            fact="197 笔,Sharpe -0.6142",
            interpretation="不构成证据",
            metrics=_BTC_1H_METRICS,
        )

    async def fake_llm(*_a, **_kw):
        # The model tries to talk the result up. It must not win.
        return (
            '{"signal":"strongly_confirming","fact":"197 笔","'
            'interpretation":"很有说服力","recommendation":"approve",'
            '"reason":"看起来不错"}'
        )

    class _Settings:
        def get_mission_worker(self):
            return {"enabled": True, "max_rounds_per_mission": 3}

    counts = await mission_worker_round(
        {"db_manager": db, "research_db": db, "settings_service": _Settings()},
        material_fn=fake_material,
        verify_fn=fake_verify,
        llm_call=fake_llm,
    )
    assert counts == {"scanned": 1, "advanced": 1, "failed": 0, "skipped": 0}

    # ② materials were assembled at the interval the anchor declared
    assert seen["material_interval"] == ["1h"]

    mission = await mrepo.get(mission_id)
    assert mission is not None
    # ④ the worker proposes; it never resolves
    assert mission["phase"] == "needs_input"

    analysis = [
        a
        for a in await mrepo.list_activity(mission_id)
        if a["kind"] == "worker_analysis"
    ]
    assert len(analysis) == 1
    detail = analysis[0]["detail"]
    # ③ the code's verdict wins over the model's self-report, and the deviation
    #    is recorded rather than silently discarded
    assert detail["signal"] == "untested"
    assert detail["signal_mismatch"] == ("llm=strongly_confirming verified=untested")
    assert detail["verification"]["ran"] is True

    # ⑤ settle: decision + child question + derived_from edge + revisit
    settlement = await settle_mission(
        db,
        mission,
        {
            "verdict": "PROBE_RESOLVED",
            "next_question": "把同一信号换到 4h 是否仍成立?",
        },
        note="probe",
    )
    assert settlement.ok
    assert settlement.decision_id
    assert settlement.child_question_id
    assert settlement.revisit_at

    qrepo = ResearchQuestionRepository(db)
    child = await qrepo.get(settlement.child_question_id)
    assert child is not None
    assert child["created_by"] == "selene"
    assert child["parent_question_id"] == question_id

    decisions = await db.execute(
        "SELECT question_id, decided_by FROM research_decisions WHERE id = ?",
        (settlement.decision_id,),
    )
    assert decisions[0]["question_id"] == question_id
    assert decisions[0]["decided_by"] == "director"

    # The derived_from edge is what makes the next question traceable.
    edges = await ResearchEdgeRepository(db).list_edges(
        settlement.child_question_id, direction="both", kind="derived_from"
    )
    assert any(
        {e["from_id"], e["to_id"]} == {settlement.child_question_id, question_id}
        for e in edges
    )

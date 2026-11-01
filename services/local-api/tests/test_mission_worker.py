"""MissionWorker 测试:investigation 相位必须有人消费,且只推进到 needs_input。

红线由这些断言钉住:
- 推进目标恒为 needs_input(绝不 validation/resolved);
- LLM 失败不推进相位;
- 轮次上限生效(防止无限烧钱);
- 非法相位不被抢。
"""

from __future__ import annotations

import json

from typing import Any

import pytest
import pytest_asyncio

from app.core.mission_verifier import VerificationOutcome
from app.core.mission_worker import (
    EXHAUSTED_KEY,
    ROUNDS_KEY,
    ROUND_ANCHOR_KEY,
    mission_worker_round,
    run_one_mission,
)
from app.services.market_material import MarketMaterial

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.mission_thesis import MissionThesisRepository
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


async def _mission(db, *, subject_id: str = "BTC/USDT") -> str:
    mission_id: str = await ResearchMissionRepository(db).create(
        title="资金费率是否已反映降息预期",
        question="BTC 资金费率是否已反映降息预期?",
        subject_type="market",
        subject_id=subject_id,
        created_by="user",
    )
    return mission_id


async def _fetch(db, mission_id: str) -> dict[str, Any]:
    """取 mission;取不到就是测试环境坏了,直接让断言失败而不是 None 下标崩。"""
    mission: dict[str, Any] | None = await ResearchMissionRepository(db).get(mission_id)
    assert mission is not None
    return mission


async def _material(db, anchor, key):
    return MarketMaterial(
        state="ok",
        reason="已注入 BTC/USDT 行情快照",
        text="最新收盘: 68000 (+2.1%), 资金费率现值: 0.000100",
        symbol="BTC/USDT",
        funding_available=True,
    )


def _ok_llm(payload: dict | None = None, *, source_seen: dict | None = None):
    body = payload or {
        "signal": "mildly_weakening",
        "fact": "资金费率由 0.00030 降至 0.00010,持仓增幅低于价格涨幅",
        "interpretation": "杠杆拥挤度下降,降息预期尚未被完全定价为单向拥挤",
        "recommendation": "approve",
        "reason": "材料支持继续验证,建议用户决定是否进入验证相位",
    }

    async def _call(settings, prompt, **kwargs):
        if source_seen is not None:
            source_seen.update(kwargs)
            source_seen["prompt"] = prompt
        return json.dumps(body, ensure_ascii=False)

    return _call


class _Settings:
    def __init__(self, section: dict | None = None) -> None:
        self._section = section or {
            "enabled": True,
            "interval_minutes": 15,
            "max_rounds_per_mission": 3,
        }

    def get_mission_worker(self) -> dict:
        return dict(self._section)


@pytest.mark.asyncio
async def test_worker_advances_investigation_to_needs_input(db) -> None:
    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)
    assert (await _fetch(db, mission_id))["phase"] == "investigation"

    result = await run_one_mission(
        db,
        mission_id,
        llm_call=_ok_llm(),
        material_fn=_material,
    )

    assert result.outcome == "advanced"
    assert result.phase == "needs_input"
    mission = await _fetch(db, mission_id)
    assert mission["phase"] == "needs_input"
    assert mission["context"][ROUNDS_KEY] == 1

    kinds = [a["kind"] for a in await repo.list_activity(mission_id)]
    assert "worker_started" in kinds
    assert "worker_analysis" in kinds
    analysis = next(
        a
        for a in await repo.list_activity(mission_id)
        if a["kind"] == "worker_analysis"
    )
    assert analysis["detail"]["recommendation"] == "approve"
    assert analysis["detail"]["proposal"].startswith("建议动作:approve")


@pytest.mark.asyncio
async def test_worker_without_verification_cannot_support_a_pillar(db) -> None:
    """没有实验 → 没有证据。模型自报 mildly_weakening 也不作数。

    2026-10-25 之前的契约是"模型判 signal",于是 53 条分析里 LLM 的口头
    说法被当成支持/反驳支柱的证据。现在只认回测:``verify_fn`` 没注入
    时验证不会执行,证据行仍然写(用户要知道这轮看了什么),但信号恒为
    ``untested`` —— 这是设计,不是降级。
    """
    mission_id = await _mission(db)
    thesis = MissionThesisRepository(db)
    pillar_id = await thesis.add_pillar(
        mission_id,
        "资金费率回落将先于价格见顶",
        "core",
        baseline="funding 0.0003",
        confirm_threshold="funding < 0.00012 连续 3 日",
        break_threshold="funding > 0.0004",
        threshold_provenance="draft",
        provenance="draft",
    )

    result = await run_one_mission(
        db, mission_id, llm_call=_ok_llm(), material_fn=_material
    )

    assert result.evidence_id is not None
    evidence = await thesis.list_evidence(mission_id)
    assert len(evidence) == 1
    assert evidence[0]["signal"] == "untested"
    assert evidence[0]["pillar_id"] == pillar_id
    assert evidence[0]["source_type"] == "worker_analysis"


@pytest.mark.asyncio
async def test_verified_signal_overrides_llm_self_report(db) -> None:
    """有回测结果时,模型说什么都不算 —— 代码判定赢,偏离留痕。"""
    mission_id = await _mission(db)
    MissionThesisRepository(db)
    await MissionThesisRepository(db).add_pillar(
        mission_id, "BTC 1h 上 rsi_ma_delta 有边缘", "core"
    )

    async def _verify(db_, mission_id_, *, anchor=None, **kwargs):
        return VerificationOutcome(
            mission_id=mission_id_,
            ran=True,
            signal="invalidating",
            reason="三项门槛全部通过",
            evidence_id="ev-verified",
            metrics={"sharpe": -1.4, "trades": 88},
        )

    result = await run_one_mission(
        db,
        mission_id,
        llm_call=_ok_llm(),  # 模型仍自报 mildly_weakening
        material_fn=_material,
        verify_fn=_verify,
    )

    assert result.evidence_id == "ev-verified"
    repo = ResearchMissionRepository(db)
    analysis = next(
        a
        for a in await repo.list_activity(mission_id)
        if a["kind"] == "worker_analysis"
    )
    assert analysis["detail"]["signal"] == "invalidating"
    assert analysis["detail"]["signal_mismatch"] == (
        "llm=mildly_weakening verified=invalidating"
    )


@pytest.mark.asyncio
async def test_worker_llm_failure_does_not_advance(db) -> None:
    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)

    async def _dead(settings, prompt, **kwargs):
        return None

    result = await run_one_mission(
        db, mission_id, llm_call=_dead, material_fn=_material
    )

    assert result.outcome == "failed"
    mission = await _fetch(db, mission_id)
    assert mission["phase"] == "investigation"
    # 失败也累加轮次,否则永远重试
    assert mission["context"][ROUNDS_KEY] == 1
    kinds = [a["kind"] for a in await repo.list_activity(mission_id)]
    assert "worker_failed" in kinds
    assert "worker_analysis" not in kinds


@pytest.mark.asyncio
async def test_worker_unparseable_output_does_not_advance(db) -> None:
    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)

    async def _garbage(settings, prompt, **kwargs):
        return "I think the market looks interesting but I will not give JSON."

    result = await run_one_mission(
        db, mission_id, llm_call=_garbage, material_fn=_material
    )
    assert result.outcome == "failed"
    assert (await _fetch(db, mission_id))["phase"] == "investigation"


@pytest.mark.asyncio
async def test_worker_round_limit_stops_extra_spend(db) -> None:
    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)
    await repo.update_context(mission_id, {ROUNDS_KEY: 3})
    calls = {"n": 0}

    async def _counting(settings, prompt, **kwargs):
        calls["n"] += 1
        return None

    result = await run_one_mission(
        db, mission_id, llm_call=_counting, material_fn=_material, max_rounds=3
    )
    assert result.outcome == "budget_exhausted"
    assert calls["n"] == 0
    kinds = [a["kind"] for a in await repo.list_activity(mission_id)]
    assert "worker_budget_exhausted" in kinds
    assert (await _fetch(db, mission_id))["phase"] == "investigation"


@pytest.mark.asyncio
async def test_exhausted_notice_is_written_once(db) -> None:
    """耗尽的 mission 每轮都会被再扫到;活动行不能跟着轮次无限增长。"""
    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)
    await repo.update_context(mission_id, {ROUNDS_KEY: 3})

    for _ in range(3):
        await run_one_mission(
            db, mission_id, llm_call=_ok_llm(), material_fn=_material, max_rounds=3
        )

    kinds = [a["kind"] for a in await repo.list_activity(mission_id)]
    assert kinds.count("worker_budget_exhausted") == 1


@pytest.mark.asyncio
async def test_user_directive_reaches_the_prompt(db) -> None:
    """用户按 more_evidence 时写的理由必须进下一轮提示词。

    回归:worker 原先只读 mission 行与支柱,活动流里的 ``user_note`` 和
    ``phase_transition`` 理由对分析完全不可见 —— 用户介入等于白说。
    """
    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)
    await repo.transition(mission_id, "needs_input", actor="selene", note="首批分析")
    await repo.transition(
        mission_id,
        "investigation",
        actor="user",
        note="只接受现货口径的费率,不要用永续",
        detail={"action": "more_evidence"},
    )
    await repo.append_activity(
        mission_id, actor="user", kind="user_note", detail={"note": "顺带看看交割月"}
    )

    seen: dict = {}
    result = await run_one_mission(
        db, mission_id, llm_call=_ok_llm(source_seen=seen), material_fn=_material
    )

    assert result.outcome == "advanced"
    prompt = seen["prompt"]
    assert "只接受现货口径的费率,不要用永续" in prompt
    assert "顺带看看交割月" in prompt
    # worker 自己那条 transition 不该被当成用户指示。
    assert "首批分析" not in prompt


@pytest.mark.asyncio
async def test_recent_user_directive_survives_a_long_activity_stream(db) -> None:
    """活动流超过读取窗口时,最近的用户指示仍必须进提示词。

    回归:仓储的 ``list_activity`` 截的是**最旧** N 行,worker 却把它当
    "最近 N 行"用。长任务(几十轮 worker_started / worker_analysis)之后,
    用户新写的指示落在窗口外 —— 表面上按钮点了、mission 也在 investigation,
    实际分析读不到任何用户输入。
    """
    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)
    await repo.transition(mission_id, "needs_input", actor="selene")
    for i in range(60):
        await repo.append_activity(
            mission_id, actor="selene", kind="worker_analysis", detail={"n": i}
        )
    await repo.transition(
        mission_id,
        "investigation",
        actor="user",
        note="改用交割月合约口径",
        detail={"action": "more_evidence"},
    )

    seen: dict = {}
    result = await run_one_mission(
        db, mission_id, llm_call=_ok_llm(source_seen=seen), material_fn=_material
    )

    assert result.outcome == "advanced"
    assert "改用交割月合约口径" in seen["prompt"]


@pytest.mark.asyncio
async def test_more_evidence_reauthorises_a_fresh_round_budget(db) -> None:
    """轮次用完后再按"继续调查",worker 必须重新开跑,而不是立刻判耗尽。

    回归:``more_evidence`` 把 mission 送回 ``investigation``,但
    ``worker_rounds`` 还是 3,于是 worker 下一轮直接返回 budget_exhausted ——
    用户按了按钮,界面显示在研究,实际永远不会有下一轮分析。
    """
    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)
    await repo.update_context(
        mission_id, {ROUNDS_KEY: 3, EXHAUSTED_KEY: "2026-01-01T00:00:00+00:00"}
    )
    await repo.transition(mission_id, "needs_input", actor="selene")
    await repo.transition(
        mission_id,
        "investigation",
        actor="user",
        note="给出新的证据方向",
        detail={"action": "more_evidence"},
    )
    calls = {"n": 0}

    async def _counting(settings, prompt, **kwargs):
        calls["n"] += 1
        return json.dumps(
            {
                "signal": "untested",
                "fact": "f",
                "interpretation": "i",
                "recommendation": "park",
                "reason": "r",
            },
            ensure_ascii=False,
        )

    result = await run_one_mission(
        db, mission_id, llm_call=_counting, material_fn=_material, max_rounds=3
    )

    assert result.outcome == "advanced"
    assert calls["n"] == 1
    mission = await _fetch(db, mission_id)
    assert mission["context"][ROUNDS_KEY] == 1


@pytest.mark.asyncio
async def test_round_budget_is_not_reset_without_a_new_user_directive(db) -> None:
    """没有新用户指示时,耗尽必须保持耗尽(否则 worker 会无限烧钱)。"""
    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)
    await repo.update_context(mission_id, {ROUNDS_KEY: 3, ROUND_ANCHOR_KEY: 999})
    # 用户指示行 id 远小于游标 999 → 视为已消费过,不重置。
    calls = {"n": 0}

    async def _counting(settings, prompt, **kwargs):
        calls["n"] += 1
        return None

    result = await run_one_mission(
        db, mission_id, llm_call=_counting, material_fn=_material, max_rounds=3
    )

    assert result.outcome == "budget_exhausted"
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_stuck_missions_do_not_starve_the_rest(db) -> None:
    """LLM 长期不可用时,前 N 条失败任务不能永远占满批次。

    回归的是 ``list_missions`` 的 ``updated_at DESC``:worker 每次碰一条
    mission(失败也累加轮次)都会抬高它的 ``updated_at``。先建的新任务若
    被 5 条反复失败的任务在库里压到第 6 位,按库序取前 5 条就永远轮不到它。
    """
    repo = ResearchMissionRepository(db)

    async def _dead(settings, prompt, **kwargs):
        return None

    # 新任务先建(此时它最新)……
    fresh = await _mission(db, subject_id="FRESH/USDT")

    # ……随后 5 条任务各失败一轮。``updated_at`` 是秒级时间戳,同一秒内建完
    # 的 6 条会并列,排序不可依赖;因此把它们显式抬到未来,稳定复现"库序
    # 前 5 名全是失败任务"。
    for i in range(5):
        mid = await _mission(db, subject_id=f"STUCK{i}/USDT")
        result = await run_one_mission(db, mid, llm_call=_dead, material_fn=_material)
        assert result.outcome == "failed"
        await db.execute(
            "UPDATE research_missions SET updated_at = ? WHERE id = ?",
            (f"2999-01-0{i + 1}T00:00:00+00:00", mid),
        )

    by_db_order = [
        m["id"] for m in await repo.list_missions(phase="investigation", limit=5)
    ]
    assert fresh not in by_db_order, "前提失效:新任务仍在库序前 5 名,回归不到饥饿"

    counts = await mission_worker_round(
        {"research_db": db},
        settings_service=_Settings(),
        llm_call=_ok_llm(),
        material_fn=_material,
    )
    assert counts["advanced"] >= 1
    assert (await _fetch(db, fresh))["phase"] == "needs_input"


@pytest.mark.asyncio
async def test_reauthorised_mission_can_exhaust_its_budget_a_second_time(db) -> None:
    """重新授权后第二次真正耗尽,必须再写一条通知。

    回归:``EXHAUSTED_KEY`` 只在"还没有这个键"时写。用户按了继续调查,轮次
    归零、标记还留着;等新的预算再次用完,条件为假 —— 活动流里一片安静,
    界面上只看到 mission 停在 investigation 不再前进。
    """
    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)
    await repo.update_context(
        mission_id, {ROUNDS_KEY: 3, EXHAUSTED_KEY: "2026-01-01T00:00:00+00:00"}
    )
    await repo.transition(mission_id, "needs_input", actor="selene")
    await repo.transition(
        mission_id,
        "investigation",
        actor="user",
        note="再查一轮",
        detail={"action": "more_evidence"},
    )

    first = await run_one_mission(
        db, mission_id, llm_call=_ok_llm(), material_fn=_material, max_rounds=3
    )
    assert first.outcome == "advanced"
    assert (await _fetch(db, mission_id))["context"][EXHAUSTED_KEY] is None

    # 同一次授权里新的轮次也用完了。用 ``actor="selene"`` 把相位送回
    # ``investigation``:只有 user 写下的行才算新指示,这样才是在复用同一份
    # 预算,而不是又按了一次"继续调查"。
    await repo.transition(mission_id, "investigation", actor="selene", note="redrive")
    await repo.update_context(mission_id, {ROUNDS_KEY: 3})
    second = await run_one_mission(
        db, mission_id, llm_call=_ok_llm(), material_fn=_material, max_rounds=3
    )
    assert second.outcome == "budget_exhausted"
    kinds = [a["kind"] for a in await repo.list_activity(mission_id)]
    assert kinds.count("worker_budget_exhausted") == 1


@pytest.mark.asyncio
async def test_raising_the_round_cap_after_a_stop_notifies_again(db) -> None:
    """调高轮次上限让 worker 重新干起来,第二次停下时也必须再通知一次。

    回归:``EXHAUSTED_KEY`` 原来只在"重新授权"时清。``max_rounds_per_mission``
    是设置项(1-20),用户把上限从 3 调到 5 就能让这条 mission 重新跑起来 ——
    但旧标记还留着,跑完新的 2 轮再停下时 ``if not context.get(EXHAUSTED_KEY)``
    为假,活动流一片安静:界面只显示"停在 investigation",没有任何解释。
    修法:一旦这一轮真的跑起来(通过预算检查),旧标记就不再成立,先清掉。
    """
    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)

    # 第一轮把预算用满并停下。
    for _ in range(3):
        await run_one_mission(
            db, mission_id, llm_call=_ok_llm(), material_fn=_material, max_rounds=3
        )
        await repo.transition(
            mission_id, "investigation", actor="selene", note="redrive"
        )
    first_stop = await run_one_mission(
        db, mission_id, llm_call=_ok_llm(), material_fn=_material, max_rounds=3
    )
    assert first_stop.outcome == "budget_exhausted"

    # 用户把上限调高 —— 不是重新授权,只是改设置。
    resumed = await run_one_mission(
        db, mission_id, llm_call=_ok_llm(), material_fn=_material, max_rounds=5
    )
    assert resumed.outcome == "advanced", "调高上限应当让这条 mission 重新跑起来"

    # 新的上限也用完了。
    await repo.transition(mission_id, "investigation", actor="selene", note="redrive")
    await repo.update_context(mission_id, {ROUNDS_KEY: 5})
    second_stop = await run_one_mission(
        db, mission_id, llm_call=_ok_llm(), material_fn=_material, max_rounds=5
    )
    assert second_stop.outcome == "budget_exhausted"
    kinds = [a["kind"] for a in await repo.list_activity(mission_id)]
    assert kinds.count("worker_budget_exhausted") == 2


@pytest.mark.asyncio
async def test_error_after_the_paid_call_still_consumes_a_round(db) -> None:
    """付费调用之后抛出的异常必须累加轮次并记失败。

    回归:``append_evidence`` 只捕获 ``(ValueError, KeyError)``,材料装配
    直接在 try 之外;这两处的任何其它异常都穿到 ``mission_worker_round``
    的兜底 except —— 轮次不动、``LAST_RUN_KEY`` 不写,而公平性排序恰好把
    "没跑过"的排最前,于是每轮都用同一条 mission 重跑一次付费分析。
    """
    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)

    async def _boom(db_, anchor, key):
        raise RuntimeError("kline store pulled out from under us")

    result = await run_one_mission(
        db, mission_id, llm_call=_ok_llm(), material_fn=_boom
    )

    assert result.outcome == "failed"
    mission = await _fetch(db, mission_id)
    assert mission["phase"] == "investigation"
    assert mission["context"][ROUNDS_KEY] == 1
    failed = [
        a for a in await repo.list_activity(mission_id) if a["kind"] == "worker_failed"
    ]
    assert len(failed) == 1
    assert failed[0]["detail"]["reason"] == "worker_error"


@pytest.mark.asyncio
async def test_crashing_mission_does_not_get_a_free_round_every_cycle(
    db, monkeypatch
) -> None:
    """付费调用之后崩溃,不能在每一轮都重新付费一遍。

    这里让 ``append_evidence``(在 LLM 之后)抛非 ``ValueError`` 异常。
    修复前:异常穿出,轮次不涨,公平性排序每轮都把它重新排到最前 ——
    三次轮询 = 三次付费调用,而 ``max_rounds`` 形同虚设。
    """
    mission_id = await _mission(db)
    thesis = MissionThesisRepository(db)
    await thesis.add_pillar(
        mission_id,
        "资金费率回落将先于价格见顶",
        "core",
        baseline="funding 0.0003",
        threshold_provenance="draft",
        provenance="draft",
    )
    calls = {"n": 0}

    async def _counting(settings, prompt, **kwargs):
        calls["n"] += 1
        return json.dumps(
            {
                "signal": "mildly_weakening",
                "fact": "f",
                "interpretation": "i",
                "recommendation": "more_evidence",
                "reason": "r",
            },
            ensure_ascii=False,
        )

    async def _explode(self, *args, **kwargs):
        raise RuntimeError("evidence table locked")

    monkeypatch.setattr(MissionThesisRepository, "append_evidence", _explode)
    for _ in range(4):
        await run_one_mission(
            db,
            mission_id,
            llm_call=_counting,
            material_fn=_material,
            max_rounds=2,
        )

    mission = await _fetch(db, mission_id)
    assert mission["context"][ROUNDS_KEY] == 2, "崩溃没有消耗轮次预算"
    assert calls["n"] == 2, f"预算上限 2 却付费调用了 {calls['n']} 次"


@pytest.mark.asyncio
async def test_worker_skips_mission_already_moved(db) -> None:
    """用户已拍板(needs_input)时,worker 不抢不覆盖。"""
    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)
    await repo.transition(mission_id, "needs_input", actor="user", note="manual")

    async def _never(settings, prompt, **kwargs):  # pragma: no cover
        raise AssertionError("LLM must not be called for a non-investigation mission")

    result = await run_one_mission(
        db, mission_id, llm_call=_never, material_fn=_material
    )
    assert result.outcome == "skipped"
    assert (await _fetch(db, mission_id))["phase"] == "needs_input"


@pytest.mark.asyncio
async def test_worker_never_reaches_validation_or_resolved(db) -> None:
    """红线:任何建议动作都只到 needs_input。"""
    for recommendation in ("approve", "park", "redirect", "more_evidence"):
        mission_id = await _mission(db)
        body = {
            "signal": "confirming",
            "fact": "事实",
            "interpretation": "解释",
            "recommendation": recommendation,
            "reason": "理由",
        }
        await run_one_mission(
            db,
            mission_id,
            llm_call=_ok_llm(body),
            material_fn=_material,
        )
        phase = (await _fetch(db, mission_id))["phase"]
        assert phase == "needs_input", f"{recommendation} advanced to {phase}"


@pytest.mark.asyncio
async def test_round_scans_investigation_and_respects_disabled(db) -> None:
    mission_id = await _mission(db)
    state = {"research_db": db}

    off = _Settings(
        {"enabled": False, "interval_minutes": 15, "max_rounds_per_mission": 3}
    )
    counts = await mission_worker_round(
        state, settings_service=off, llm_call=_ok_llm(), material_fn=_material
    )
    assert counts == {"scanned": 0, "advanced": 0, "failed": 0, "skipped": 0}
    assert (await _fetch(db, mission_id))["phase"] == "investigation"

    counts = await mission_worker_round(
        state, settings_service=_Settings(), llm_call=_ok_llm(), material_fn=_material
    )
    assert counts["advanced"] == 1
    assert (await _fetch(db, mission_id))["phase"] == "needs_input"


@pytest.mark.asyncio
async def test_loop_advances_once_then_reads_interval_from_settings(db) -> None:
    """循环每轮重读 mission_worker 设置:间隔取自设置,关停可取消。"""
    import asyncio

    from app.core.mission_worker import mission_worker_loop

    mission_id = await _mission(db)
    seen: dict = {}

    def _settings():
        seen["reads"] = seen.get("reads", 0) + 1
        return {
            "enabled": True,
            "interval_minutes": 1,
            "max_rounds_per_mission": 3,
        }

    class _LiveSettings:
        get_mission_worker = staticmethod(_settings)

    state = {"research_db": db, "settings_service": _LiveSettings()}

    task = asyncio.create_task(
        mission_worker_loop(
            state,
            default_interval=3600.0,
            llm_call=_ok_llm(),
            material_fn=_material,
        )
    )
    try:
        for _ in range(200):
            if (await _fetch(db, mission_id))["phase"] == ("needs_input"):
                break
            await asyncio.sleep(0.01)
        assert (await _fetch(db, mission_id))["phase"] == ("needs_input")
        assert seen.get("reads", 0) >= 1, "loop must re-read settings each round"
        assert not task.done(), "loop must keep sleeping between rounds"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_concurrent_consumers_buy_only_one_analysis(db) -> None:
    """同一条 mission 被两个消费方同时取走时,付费 LLM 只准调一次。

    回归 2026-10-21:相位检查在入口、推进在付费调用之后,中间那段窗口里
    第二个消费方看到同样的 ``investigation``,于是同一条被分析两遍。实测
    13 条 mission 各被分析两次,多付 20 次调用,活动流里 ``worker_started``
    ×3 却只有一条 ``phase_transition``。共享 ``~/.selenespace`` 的多副本
    同样会撞上,所以锁必须是库级(条件 UPDATE),不是进程内 Lock。
    """
    import asyncio

    mission_id = await _mission(db)
    repo = ResearchMissionRepository(db)
    counter: dict = {"calls": 0}

    def _counting_llm():
        ok = _ok_llm()

        async def _call(settings, prompt, **kwargs):
            counter["calls"] = counter.get("calls", 0) + 1
            await asyncio.sleep(0.05)  # 拉开窗口,让两个消费方真正重叠
            return await ok(settings, prompt, **kwargs)

        return _call

    r1, r2 = await asyncio.gather(
        run_one_mission(
            db, mission_id, llm_call=_counting_llm(), material_fn=_material
        ),
        run_one_mission(
            db, mission_id, llm_call=_counting_llm(), material_fn=_material
        ),
    )

    assert (
        counter["calls"] == 1
    ), f"付费 LLM 被调用了 {counter['calls']} 次,一条 mission 的一轮只该买一次"
    assert {r1.outcome, r2.outcome} == {"advanced", "skipped"}

    activities = await repo.list_activity(mission_id)
    kinds = [a["kind"] for a in activities]
    assert kinds.count("worker_started") == 1
    assert kinds.count("phase_transition") == 1

    mission = await _fetch(db, mission_id)
    assert mission["phase"] == "needs_input"
    # 认领必须在退出时释放,不能靠 15 分钟 TTL 兜底 —— 留着的话这条在 TTL
    # 内谁都跑不了(下一轮会被误判成"有人正在跑")。
    assert "worker_claim" not in mission["context"]

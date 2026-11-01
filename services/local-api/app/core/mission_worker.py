"""MissionWorker:``investigation`` 相位的消费者。

mission 进入 ``investigation`` 有两处来源:promote 出生,或用户在
``needs_input`` 上选 more_evidence / redirect。没有消费者时,这条相位就是
黑洞 —— 界面上表现为"我点了继续调查,然后什么都没有发生"。

本模块每轮对一条 mission 做六件事:

1. 装配真实材料(与调查会同源:本地 K 线按 **anchor 声明的周期**,+ 资金费率);
2. 读用户最近给的指示(``user_note`` 留言、``more_evidence`` / ``redirect``
   的理由)并放进提示词 —— 不读的话用户介入等于白说;
3. **跑验证**(2026-10-25 新增):若这条 mission 有可执行的策略目标,调
   :func:`app.core.mission_verifier.verify_mission` 跑一次真实回测,由代码
   按预先声明的阈值把结果判成 signal 并写 evidence。**判定权在代码,不在模型。**
4. 调**一次** LLM 做解释(``source="mission_worker"``,受每日 token 上限约束);
   LLM 读的是回测数字,产出的是"这意味着什么"与"建议下一步",不是"算不算支持";
5. 把分析写进活动流;有活跃 pillar 且第 3 步没产出证据时,才由 LLM 落一行
   evidence(那种情况恒为 ``untested`` —— 没有实验就没有证据);
6. 把 mission 推进到 ``needs_input`` —— **提出决策,等用户拍板**。

红线(用户明确要求"自动跑到提出决策就停"):

- 绝不 transition 到 ``validation`` / ``resolved``;
- 绝不下单、绝不部署、绝不 approve 任何东西;
- LLM 失败或返回不可解析时**不推进相位**,只记一条 ``worker_failed``;
- 每任务轮次记在 ``context.worker_rounds``,到上限即停(防止同一 mission
  无限循环烧钱);轮次在失败时也累加,否则失败的 mission 会永远重试。
- 预算按"一次调查授权"计:用户在 needs_input 上重新要求调查(新的一条
  user 指示)才重置轮次。worker 自己不给自己续预算。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Literal

from selene_space_storage.repositories._mission_thesis_vocab import SIGNALS
from selene_space_storage.repositories.mission_thesis import MissionThesisRepository
from selene_space_storage.repositories.research_graph import (
    ResearchQuestionRepository,
)
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)

from app.core.mission_binding import mission_question_id
from app.core.mission_verifier import verify_mission
from app.services.market_material import assemble_market_material

logger = logging.getLogger(__name__)

#: 轮次计数键(存在 mission context 里,零迁移)。
ROUNDS_KEY = "worker_rounds"
#: 最后一次真正跑分析的时刻。选批次用它排序,不看 ``updated_at``。
LAST_RUN_KEY = "worker_last_run_at"
#: "轮次已耗尽"只记一次活动。不然失败的 mission 每轮都会再追加一行。
EXHAUSTED_KEY = "worker_exhausted_at"
#: 上一轮已经消费到的用户指示(活动行 id)。见 ``run_one_mission`` 里的重置说明。
ROUND_ANCHOR_KEY = "worker_round_anchor"

_MAX_TOKENS = 700

_SYSTEM = (
    "You are a research analyst. You are given a research mission, its falsifiable "
    "pillars, and real market material. Produce ONE evidence-based analysis. "
    "You never make trading decisions and never claim the mission is resolved; "
    "you only tell the human what the evidence says and what they should decide next. "
    "Reply with a single compact JSON object and nothing else. Do not restate the input."
)

# 长度硬约束不是文风问题,是**可用性问题**。实测(BTC 1h 真实回测数字,同一
# prompt 各 6 次):没有长度限制时,推理模型的 thinking 与正文共用
# ``max_tokens``,一半的调用被截在 JSON 中间(finish_reason=length,HTTP 200),
# 于是 worker 记成 "unparseable_response" 并空转一整轮 —— 生产库里 5 条
# worker_failed 全是这个形状。加上每字段字数上限后同一档预算 6/6 可解析,
# 输出 token 从 674~1600 降到 102~286。
_PROMPT_TEMPLATE = """研究任务:{title}
待回答的问题:{question}
主体:{subject_type} {subject_id}

用户最近给的指示(优先级高于你自己的判断;若与下面材料冲突,以用户指示为准):
{directives}

支柱(可证伪陈述):
{pillars}

行情材料:
{material}

验证结果(真实回测,由代码按预先声明的阈值判定):
{verification}

请输出严格 JSON,每个字段都不许超过字数上限(超了会被截断,整轮作废):
- "signal": 必须取自 {signals} 之一。**若上面已有验证结果,原样照抄它的
  signal,不要自己重新判断** —— 判定权在代码;没有验证结果时才用 "untested"
- "fact": 只陈述材料/验证结果里查得到的事实(不推断),<= 60 字
- "interpretation": 这些事实对支柱意味着什么(可证伪地表述),<= 80 字
- "recommendation": 下一步建议动作,取自 approve|more_evidence|redirect|park
- "reason": 一句话说明为什么建议这个动作,<= 50 字

没有验证结果时不要编造数字;材料不足以判断就用 signal="untested",
并在 reason 里说明缺什么。不要输出任何 JSON 以外的文字。"""

#: 没有验证结果时给提示词的一句实话(而不是空字符串,模型会当"忘了")。
_NO_VERIFICATION_TEXT = "(本轮没有可执行的回测验证 — 不要据此断言支柱被支持或反驳)"


@dataclass(frozen=True)
class WorkerResult:
    """一次 run_one_mission 的结局(供日志与测试断言)。"""

    mission_id: str
    outcome: Literal["advanced", "budget_exhausted", "failed", "skipped"]
    phase: str
    evidence_id: str | None = None
    proposal: str | None = None


LlmCall = Callable[..., Awaitable[str | None]]
MaterialFn = Callable[[Any, dict | None, str], Awaitable[Any]]
VerifyFn = Callable[..., Awaitable[Any]]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _extract_json(text: str) -> dict[str, Any] | None:
    """从可能带代码围栏的回复里抠出第一个 JSON 对象。"""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        candidate = text[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def _resolve_anchor(db: Any, mission: dict[str, Any]) -> tuple[dict | None, str]:
    """材料装配用锚 + 给材料模块用的问题标识。

    promote 出生的 mission 携带 ``context.question_id``,沿着它取问题的
    ``anchor.(symbols|intervals)``;手建 market mission 没有问题,就按主体标的
    造锚 —— **并继承 context 里声明的 ``interval``**(手建时不带的话装配层会
    退回 1d,而这正是 2026-10-25 修掉的那个周期错配)。
    """
    question_id = await mission_question_id(db, mission)
    if question_id:
        question = await ResearchQuestionRepository(db).get(str(question_id))
        if question is not None and question.get("anchor"):
            return question["anchor"], str(question_id)
    ctx = mission.get("context") or {}
    if mission.get("subject_type") == "market":
        anchor: dict[str, Any] = {"symbols": [str(mission.get("subject_id") or "")]}
        interval = str(ctx.get("interval") or "").strip()
        if interval:
            anchor["intervals"] = [interval]
        if ctx.get("strategy_id"):
            anchor["strategy_ids"] = [str(ctx["strategy_id"])]
        return anchor, f"mission:{mission['id']}"
    return None, f"mission:{mission['id']}"


def _render_pillars(pillars: list[dict[str, Any]]) -> str:
    active = [
        p
        for p in pillars
        if p.get("superseded_by") is None and p.get("status") != "retired"
    ]
    if not active:
        return "(无活跃支柱)"
    lines = []
    for p in active:
        confirm = p.get("confirm_threshold")
        brk = p.get("break_threshold")
        tail = ""
        if confirm:
            tail += f" 确认阈值:{confirm}"
        if brk:
            tail += f" 破位阈值:{brk}"
        lines.append(f"- [{p.get('priority')}] {p.get('claim')}{tail}")
    return "\n".join(lines)


def _render_verification(verification: Any) -> str:
    """把 ``VerificationOutcome``(或 None)压成提示词里的一段。

    只给结论与数字,不给"你觉得呢" —— 见 ``_PROMPT_TEMPLATE`` 里对 signal
    的指令:有验证结果时模型照抄,不重新判断。
    """
    if verification is None:
        return _NO_VERIFICATION_TEXT
    ran = bool(getattr(verification, "ran", False))
    reason = str(getattr(verification, "reason", "") or "")
    if not ran:
        return f"(本轮验证未执行:{reason or '无可用策略目标'})\n{_NO_VERIFICATION_TEXT}"
    metrics = getattr(verification, "metrics", None) or {}
    signal = getattr(verification, "signal", "untested")
    gates = getattr(verification, "gates", ()) or ()
    lines = [
        f"- 结论 signal: {signal}(由代码阈值判定,请照抄)",
        f"- 事实: {getattr(verification, 'fact', '')}",
        f"- 解释: {getattr(verification, 'interpretation', '')}",
    ]
    if gates:
        gate_text = " | ".join(
            f"{g.name}={'pass' if g.passed else 'fail'}({g.reason})" for g in gates
        )
        lines.append(f"- 门槛: {gate_text}")
    if metrics:
        lines.append(
            "- 原始数字: "
            + ", ".join(
                f"{k}={metrics[k]}"
                for k in (
                    "bars",
                    "trades",
                    "sharpe",
                    "permutation_p",
                    "ci_lower",
                    "ci_upper",
                )
                if metrics.get(k) is not None
            )
        )
    return "\n".join(lines)


# 哪些活动行算"人的指示":留言端点写的 ``user_note``,以及 actor=user 的
# ``phase_transition``(more_evidence / redirect 的理由存在 ``note`` 里)。
_DIRECTIVE_KINDS = ("user_note", "phase_transition")
#: 只看最近的若干条:更早的轮次已经被之前的分析消费过了。
_DIRECTIVE_SCAN = 50
_DIRECTIVE_LIMIT = 5
_DIRECTIVE_MAX_CHARS = 600


def _render_directives(activity: list[dict[str, Any]]) -> str:
    """把用户最近说的话摘成提示词里的一段。

    只取最近几条,防止老指令撑爆上下文;每条标明来源种类,让模型分得清
    "用户要求的方向"和"用户上一次的动作"。"""
    directives: list[str] = []
    for row in reversed(activity):
        if row.get("actor") != "user" or row.get("kind") not in _DIRECTIVE_KINDS:
            continue
        detail = row.get("detail") or {}
        text = str(detail.get("note") or "").strip()
        if row.get("kind") == "user_note":
            label = "留言"
        else:
            action = str(detail.get("action") or "").strip()
            label = f"决策 {action}" if action else "决策"
        if not text:
            continue
        if len(text) > _DIRECTIVE_MAX_CHARS:
            text = text[:_DIRECTIVE_MAX_CHARS] + "…"
        directives.append(f"- [{label}] {text}")
        if len(directives) >= _DIRECTIVE_LIMIT:
            break
    if not directives:
        return "(用户没有额外指示)"
    directives.reverse()  # 旧的在前,新的在后
    return "\n".join(directives)


def _last_directive_id(activity: list[dict[str, Any]]) -> int:
    """最近一条用户指示的活动行 id(没有则 0)。

    用作"这次调查授权从哪开始"的游标:新用户指示 = 新授权 = 轮次预算重置。
    """
    for row in reversed(activity):
        if row.get("actor") == "user" and row.get("kind") in _DIRECTIVE_KINDS:
            return int(row.get("id") or 0)
    return 0


async def run_one_mission(
    db: Any,
    mission_id: str,
    *,
    settings_service: Any = None,
    llm_call: LlmCall | None = None,
    material_fn: MaterialFn | None = None,
    verify_fn: VerifyFn | None = None,
    max_rounds: int = 3,
) -> WorkerResult:
    """把一条 ``investigation`` mission 推进到 ``needs_input``(或停下)。

    ``llm_call`` / ``material_fn`` / ``verify_fn`` 可注入,测试不碰网络、
    真实行情库或真实回测。
    """
    repo = ResearchMissionRepository(db)
    mission = await repo.get(mission_id)
    if mission is None:
        return WorkerResult(mission_id, "skipped", "missing")

    phase = str(mission.get("phase"))
    if phase != "investigation":
        # 别人已经推进了(用户拍板 / 另一轮 worker),不抢。
        return WorkerResult(mission_id, "skipped", phase)

    activity = await repo.list_recent_activity(mission_id, limit=_DIRECTIVE_SCAN)

    context = mission.get("context") or {}
    rounds = int(context.get(ROUNDS_KEY) or 0)
    # 轮次上限是"一次调查授权的预算",不是"这条 mission 一辈子的预算"。
    # 用户重新要求调查 = 新的授权;不清零的话 worker 会立刻判耗尽,按钮形同虚设。
    # 用活动行 id 而不是时间戳游标:``occurred_at`` 是秒级,同秒写入会并列。
    # 只有 user 写的行算数,所以 worker 不会给自己续预算。
    last_directive_id = _last_directive_id(activity)
    context_update: dict[str, Any] = {}
    if last_directive_id > int(context.get(ROUND_ANCHOR_KEY) or 0):
        rounds = 0
        # 立刻落游标:即使这一轮 LLM 失败,下次也不该再白捡一次新预算。
        context_update[ROUND_ANCHOR_KEY] = last_directive_id
    if rounds >= max_rounds:
        # 只记一次。每轮都记会让一条耗尽的任务无限追加活动行。
        if not context.get(EXHAUSTED_KEY):
            await repo.update_context(mission_id, {EXHAUSTED_KEY: _now()})
            await repo.append_activity(
                mission_id,
                actor="system",
                kind="worker_budget_exhausted",
                detail={"rounds": rounds, "max_rounds": max_rounds},
            )
        return WorkerResult(mission_id, "budget_exhausted", phase)

    # 走到这里说明这一轮真的要跑了 —— 旧标记已经不再描述现实。它可能来自
    # 上一次耗尽,而用户后来把 ``max_rounds_per_mission`` 调高了(设置项,可改)。
    # 不清的话,等新的上限也用完时 ``if not context.get(EXHAUSTED_KEY)`` 为假,
    # 第二次停下悄无声息,界面只看到 mission 停在 investigation。
    if context.get(EXHAUSTED_KEY):
        context_update[EXHAUSTED_KEY] = None

    async def _run_claimed_round() -> WorkerResult:
        """预算检查之后的全部工作。

        包成一个单元,是为了让**任何**未预期异常走同一条收尾:累加轮次 +
        记一行 ``worker_failed``。此前只有 LLM 失败与 JSON 解析失败会累加
        轮次;``append_evidence``(付费调用之后)或 ``assemble_market_material``
        抛出的其它异常直接穿到 ``mission_worker_round`` 的兜底 except —— 轮次
        没动、``LAST_RUN_KEY`` 没写,而 ``_fairness_key`` 恰好把"没跑过"的排
        最前,于是下一轮第一个又是它:每轮一次付费 LLM 调用,永不收敛。
        """
        await repo.append_activity(
            mission_id, actor="selene", kind="worker_started", detail={"rounds": rounds}
        )

        anchor, material_key = await _resolve_anchor(db, mission)
        assemble = material_fn or assemble_market_material
        material = await assemble(db, anchor, material_key)
        material_text = getattr(material, "text", None) or "(无行情材料)"
        material_detail = material.to_detail() if hasattr(material, "to_detail") else {}

        thesis_repo = MissionThesisRepository(db)
        pillars = await thesis_repo.list_pillars(mission_id)

        # 先验证,后解释。顺序不能反:如果 LLM 先看到"问题 + 材料"就写一段
        # "我认为",再让它看回测,它会把自己的判断和数字混起来。这里让模型
        # 只拿到已经判好的结论,它的工作被压缩成"解释 + 建议下一步"。
        verify = verify_fn or verify_mission
        verification = None
        try:
            verification = await verify(db, mission_id, anchor=anchor)
        except Exception as exc:  # noqa: BLE001 — 验证失败不连坐整轮
            logger.warning(
                "mission verifier crashed for %s: %s", mission_id, exc, exc_info=True
            )
        verification_text = _render_verification(verification)

        prompt = _PROMPT_TEMPLATE.format(
            title=mission.get("title") or "",
            question=mission.get("question") or "",
            subject_type=mission.get("subject_type") or "",
            subject_id=mission.get("subject_id") or "",
            directives=_render_directives(activity),
            pillars=_render_pillars(pillars),
            material=material_text,
            verification=verification_text,
            signals="|".join(SIGNALS),
        )

        call = llm_call
        if call is None:
            from app.core.llm_helper import call_llm

            call = call_llm
        try:
            raw = await call(
                settings_service,
                prompt,
                system=_SYSTEM,
                max_tokens=_MAX_TOKENS,
                source="mission_worker",
            )
        except Exception as exc:  # noqa: BLE001 — worker 不得因单次失败拖垮整轮
            logger.warning("mission worker LLM call failed for %s: %s", mission_id, exc)
            raw = None

        parsed = _extract_json(raw) if raw else None
        if parsed is None:
            await repo.update_context(
                mission_id,
                {**context_update, ROUNDS_KEY: rounds + 1, LAST_RUN_KEY: _now()},
            )
            await repo.append_activity(
                mission_id,
                actor="selene",
                kind="worker_failed",
                detail={
                    "reason": "llm_unavailable" if not raw else "unparseable_response",
                    "rounds": rounds + 1,
                    "raw_excerpt": (raw or "")[:400],
                    "material": material_detail,
                },
            )
            return WorkerResult(mission_id, "failed", phase)

        signal = str(parsed.get("signal") or "").strip()
        fact = str(parsed.get("fact") or "").strip()
        interpretation = str(parsed.get("interpretation") or "").strip()
        recommendation = str(parsed.get("recommendation") or "").strip()
        reason = str(parsed.get("reason") or "").strip()
        if recommendation not in ("approve", "more_evidence", "redirect", "park"):
            recommendation = "more_evidence"

        active_pillars = [
            p
            for p in pillars
            if p.get("superseded_by") is None and p.get("status") != "retired"
        ]

        # ---- 证据来源:只认回测;LLM 的话只能落 untested ----------------
        # 回测已写证据时,让**它**决定 signal(模型拿到的 prompt 也要求照抄,
        # 但 prompt 不是保证)。模型没照抄 → 以回测为准,并把偏离记进活动流,
        # 否则一次措辞就能把"不构成证据"改写成"强烈支持"。
        verified_signal = (
            getattr(verification, "signal", None)
            if verification is not None and getattr(verification, "ran", False)
            else None
        )
        verified_evidence_id = (
            getattr(verification, "evidence_id", None)
            if verification is not None
            else None
        )
        signal_mismatch: str | None = None
        evidence_id: str | None = verified_evidence_id
        evidence_error: str | None = None

        if verified_signal:
            if signal in SIGNALS and signal != verified_signal:
                signal_mismatch = f"llm={signal} verified={verified_signal}"
            signal = verified_signal
            fact = getattr(verification, "fact", "") or fact
            interpretation = (
                getattr(verification, "interpretation", "") or interpretation
            )
        elif active_pillars and fact and interpretation:
            # 没有回测 → 没有任何东西能支持或反驳支柱。仍然留一行证据
            # (让用户知道"这轮看了什么"),但信号恒为 untested,不管模型
            # 自报了什么。这是 2026-10-25 之前 53 条全 untested 的显式版本:
            # 当时是"模型自己承认测不了",现在是"系统规定没实验就不算证据"。
            try:
                evidence_id = await thesis_repo.append_evidence(
                    mission_id,
                    active_pillars[0]["id"],
                    occurred_at=_now(),
                    source_type="worker_analysis",
                    fact=fact,
                    interpretation=interpretation,
                    signal="untested",
                    source_ref=f"mission_worker:{mission_id}",
                    follow_up=reason or None,
                    owner="selene",
                )
            except (ValueError, KeyError) as exc:
                evidence_error = str(exc)
                logger.warning(
                    "mission worker evidence rejected for %s: %s", mission_id, exc
                )
            signal = "untested"

        proposal = (
            f"建议动作:{recommendation}"
            + (f" — {reason}" if reason else "")
            + (f"\n信号:{signal}" if signal else "")
        )
        await repo.append_activity(
            mission_id,
            actor="selene",
            kind="worker_analysis",
            detail={
                "proposal": proposal,
                "recommendation": recommendation,
                "reason": reason,
                "signal": signal,
                "signal_mismatch": signal_mismatch,
                "evidence_id": evidence_id,
                "evidence_error": evidence_error,
                "verification": (
                    verification.to_detail()
                    if verification is not None and hasattr(verification, "to_detail")
                    else None
                ),
                "material": material_detail,
                "rounds": rounds + 1,
            },
        )
        await repo.update_context(
            mission_id,
            {**context_update, ROUNDS_KEY: rounds + 1, LAST_RUN_KEY: _now()},
        )

        try:
            await repo.transition(
                mission_id,
                "needs_input",
                actor="selene",
                note=f"worker proposed {recommendation}",
                detail={"proposal": proposal, "evidence_id": evidence_id},
            )
        except ValueError as exc:
            # 相位竞赛(用户已处置)。分析仍留在活动流里,不当作失败。
            logger.info("mission %s transition skipped: %s", mission_id, exc)
            updated = await repo.get(mission_id)
            return WorkerResult(
                mission_id,
                "skipped",
                str((updated or {}).get("phase") or phase),
                evidence_id,
                proposal,
            )

        return WorkerResult(
            mission_id, "advanced", "needs_input", evidence_id, proposal
        )

    # 认领这一轮。相位检查在函数入口,而真正的相位推进要等付费 LLM 返回之后 ——
    # 中间这段窗口里第二个消费方(同一循环重叠的一轮、共享同一个
    # ``~/.selenespace`` 的另一副本)会看到同样的 ``investigation``,于是同一条
    # mission 被付费分析两遍,只有一次 transition 落地。实测 2026-10-21:13 条
    # mission 各被分析两次,多付 20 次调用,活动流里 ``worker_started`` ×3 却
    # 只有一条 ``phase_transition``。认领是单条条件 UPDATE,只有一方能拿到。
    claim_token = uuid.uuid4().hex
    if not await repo.claim_worker_round(mission_id, token=claim_token, now=_now()):
        return WorkerResult(mission_id, "skipped", phase)

    try:
        return await _run_claimed_round()
    except Exception as exc:  # noqa: BLE001 — 一轮里任何未预期异常都必须消耗预算
        logger.warning("mission worker crashed on %s", mission_id, exc_info=True)
        try:
            # 先落轮次再记活动:即便活动写入失败,预算也已经被消耗,
            # 下一次不会再用同一条 mission 重跑一遍付费分析。
            await repo.update_context(
                mission_id,
                {**context_update, ROUNDS_KEY: rounds + 1, LAST_RUN_KEY: _now()},
            )
            await repo.append_activity(
                mission_id,
                actor="selene",
                kind="worker_failed",
                detail={
                    "reason": "worker_error",
                    "error": f"{type(exc).__name__}: {exc}"[:400],
                    "rounds": rounds + 1,
                },
            )
        except Exception:  # noqa: BLE001 — 收尾失败不连坐整轮扫描
            logger.warning(
                "mission worker could not record failure for %s",
                mission_id,
                exc_info=True,
            )
        return WorkerResult(mission_id, "failed", phase)
    finally:
        # 释放必须覆盖所有出口(成功推进、LLM 失败、崩溃):留着会让这条 mission
        # 在 TTL 内谁都跑不了。TTL 只是崩溃兜底,不是正常路径。
        try:
            await repo.release_worker_claim(mission_id, token=claim_token)
        except Exception:  # noqa: BLE001 — 释放失败不覆盖本轮真实结局
            logger.warning(
                "mission worker could not release claim for %s",
                mission_id,
                exc_info=True,
            )


#: 单轮最多处理多少条,防止积压时一次跑太久。
_BATCH_LIMIT = 5
#: 候选池 = 批次 × 该倍数,再按"最久没跑"排序截断。没有它,库序前 N 条
#: 永远占满名额(见 ``mission_worker_round`` 里的饥饿说明)。
_SCAN_MULTIPLIER = 4


async def mission_worker_round(
    state: dict,
    *,
    settings_service: Any = None,
    llm_call: LlmCall | None = None,
    material_fn: MaterialFn | None = None,
    verify_fn: VerifyFn | None = None,
    now: str | None = None,
) -> dict[str, int]:
    """扫描 ``investigation`` 相位的 mission 并逐条推进。

    每轮重读设置(关闭后无需重启即生效)。``now`` 仅为保持调用面与
    revisit watcher 一致,本函数不用时间判定。
    """
    db = state.get("research_db") or state.get("db_manager")
    if db is None:
        return {"scanned": 0, "advanced": 0, "failed": 0, "skipped": 0}

    settings = settings_service or state.get("settings_service")
    max_rounds = 3
    if settings is not None:
        section = settings.get_mission_worker()
        if not section.get("enabled", True):
            return {"scanned": 0, "advanced": 0, "failed": 0, "skipped": 0}
        max_rounds = int(section.get("max_rounds_per_mission") or max_rounds)

    repo = ResearchMissionRepository(db)

    # ``list_missions`` 按 ``updated_at DESC`` 排 —— 而 worker 每碰一条 mission
    # 都会抬高它的 ``updated_at``(update_context / transition / append_activity
    # 全部写它)。若直接按库序取前 N 条,LLM 长期不可用时,前 N 条失败的
    # mission 会每一轮都占据全部名额,后面的任务永远轮不到 —— 积压越多越饿。
    # 这里按"上轮是否真的跑过分析"重新排序:没跑过的(新任务 / 用户刚按
    # more_evidence)优先,其次是最久没跑的。用 worker 自己的键而不是
    # ``updated_at``,以免用户留言把任务顶回队首。
    def _fairness_key(mission: dict[str, Any]) -> tuple[int, str]:
        ctx = mission.get("context") or {}
        last = str(ctx.get(LAST_RUN_KEY) or "")
        return (0 if not last else 1, last)

    # 多取一些候选再排序,否则"只看最旧的 N 条"仍会被同一批占满。
    candidates = await repo.list_missions(
        phase="investigation", limit=_BATCH_LIMIT * _SCAN_MULTIPLIER
    )
    missions = sorted(candidates, key=_fairness_key)[:_BATCH_LIMIT]
    counts = {"scanned": len(missions), "advanced": 0, "failed": 0, "skipped": 0}
    for mission in missions:
        try:
            result = await run_one_mission(
                db,
                str(mission["id"]),
                settings_service=settings,
                llm_call=llm_call,
                material_fn=material_fn,
                verify_fn=verify_fn,
                max_rounds=max_rounds,
            )
        except asyncio.CancelledError:  # pragma: no cover - 关停路径
            raise
        except Exception:  # noqa: BLE001 — 单条异常不连坐整轮
            logger.warning(
                "mission worker failed on %s", mission.get("id"), exc_info=True
            )
            counts["failed"] += 1
            continue
        if result.outcome == "advanced":
            counts["advanced"] += 1
        elif result.outcome == "failed":
            counts["failed"] += 1
        else:
            counts["skipped"] += 1
    return counts


async def mission_worker_loop(
    state: dict,
    *,
    default_interval: float,
    llm_call: Any | None = None,
    material_fn: Any | None = None,
    verify_fn: Any | None = None,
) -> None:
    """周期跑 worker;关闭时只是空转(每轮重读设置)。

    间隔也每轮重读 —— 用户在设置页把 15 分钟改成 1 分钟,下一轮就生效,
    不需要重启。读不到合法值时退回 ``default_interval``。
    """
    while True:
        interval = default_interval
        try:
            counts = await mission_worker_round(
                state,
                llm_call=llm_call,
                material_fn=material_fn,
                verify_fn=verify_fn,
            )
            if counts["advanced"] or counts["failed"]:
                logger.info(
                    "mission worker: %d scanned, %d advanced, %d failed",
                    counts["scanned"],
                    counts["advanced"],
                    counts["failed"],
                )
            settings = state.get("settings_service")
            if settings is not None:
                minutes = settings.get_mission_worker().get("interval_minutes")
                if isinstance(minutes, int) and 1 <= minutes <= 1440:
                    interval = float(minutes) * 60.0
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("mission worker round failed", exc_info=True)
        await asyncio.sleep(interval)


__all__ = [
    "EXHAUSTED_KEY",
    "ROUNDS_KEY",
    "ROUND_ANCHOR_KEY",
    "WorkerResult",
    "mission_worker_loop",
    "mission_worker_round",
    "run_one_mission",
]

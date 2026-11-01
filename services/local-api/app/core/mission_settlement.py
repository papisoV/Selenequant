"""MissionSettlement:把"结案"从一次状态翻转变成一轮研究的起点。

**它补的是第 ⑤ 步。** 结案(``resolve``)原先只做两件事:写 ``outcome_json``、
把 phase 推到 ``resolved``。于是:

- 结论没有进 ``research_decisions`` ——  Decisions 表里只有 2026-10-03 的两条老记录,
  之后每次结案都是审计链上的一个洞;
- 没有派生下一个问题 —— 用户结掉一条 mission,"接下来查什么"仍要人重新想;
- 没有复访时点 —— 结论说过的话永远不会被回头检验。

本模块把这三件在**一次调用里**做完,并且顺序固定:

1. ``DecisionRepository.record`` 写正式判断(``decided_by`` 恒为 director ——
   schema 层就写不进别的值,这是设计不是参数);观察窗口取结论里声明的
   ``window_start/window_end``,没有就按 ``DEFAULT_WINDOW_DAYS`` 算;
2. 若结论声明了 ``next_question``,用 ``ResearchQuestionRepository.create``
   建一条 ``created_by='selene'`` 的子问题(带 parent + anchor),再写一条
   ``derived_from`` 事实边 —— **不自动开 mission**:开调查是用户的按钮
   (与 promote 同一条红线),系统只把候选问题摆出来;
3. 在原问题上设 ``next_revisit_at``,到点由 ``revisit_watcher`` 浮出待办;
4. 把这一切作为一条 ``outcome_recorded`` 之外的 ``settlement`` 活动行写回
   mission,界面能看到"结案带了什么"。

**失败语义。** 四步都是"能写就写",失败进 ``warnings`` 且**不抛异常**:

- 真实库里 36/37 条 mission 没有 ``context.question_id``(老开案路径不挂问题)。
  若缺问题就抛,那些人手里的 validation mission 会永远结不了案 ——
  一个为了审计完整性写死的门,把用户挡在门外,是更糟的失败。
  缺问题时决策跳过、``warning`` 明说是"结论只进了 mission 自身",不静默吞掉。
- 半截回滚会让"已经宣告给用户的结论"凭空消失,比缺一条复访更糟,
  所以后续三步失败一律不回滚已写成的决策。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from selene_space_storage.repositories.mission_thesis import MissionThesisRepository
from selene_space_storage.repositories.research_graph import (
    DecisionRepository,
    ResearchEdgeRepository,
    ResearchQuestionRepository,
)

logger = logging.getLogger(__name__)

#: ``next_proof_point`` 在某些老数据里被当成"下一个验证日期"存(见
#: ``mission_thesis.list_overdue_proof_points`` 的日期解析)。当作问题标题会把
#: "2026-09-10" 变成一条无意义的子问题 —— 这种值不派生,留给结论显式声明。
_BARE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: 结案时没声明观察窗口的默认长度。30 天足够覆盖一次月度级别的验证,
#: 又不至于长到永远等不到结果。
DEFAULT_WINDOW_DAYS = 30
#: 复访默认在观察窗口结束后再过多少天。给"窗口刚结束、数据还没整理"留缓冲。
REVISIT_AFTER_WINDOW_DAYS = 7


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class SettlementResult:
    """结案产物;``decision_id`` 为 None 表示主产物失败。"""

    mission_id: str
    decision_id: str | None = None
    child_question_id: str | None = None
    revisit_at: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.decision_id is not None

    def to_detail(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "child_question_id": self.child_question_id,
            "revisit_at": self.revisit_at,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "warnings": list(self.warnings),
        }


def _resolve_window(outcome: dict[str, Any], decided_at: datetime) -> tuple[str, str]:
    """观察窗口:结论里显式声明的优先,否则按默认天数从结案日算起。"""
    start = _parse_dt(outcome.get("window_start"))
    end = _parse_dt(outcome.get("window_end"))
    if start is not None and end is not None and end > start:
        return _iso(start), _iso(end)
    start = start or decided_at
    end = end or (start + timedelta(days=DEFAULT_WINDOW_DAYS))
    if end <= start:
        end = start + timedelta(days=DEFAULT_WINDOW_DAYS)
    return _iso(start), _iso(end)


def _next_question_spec(
    outcome: dict[str, Any], pillar_proof: str | None = None
) -> dict[str, Any] | None:
    """结论里声明的"下一步该查什么";没声明时退到支柱的 ``next_proof_point``。

    接受两种写法,避免调用方要记一套格式:

    - ``{"next_question": {"title": ..., "body": ..., "anchor": {...}}}``
    - ``{"next_question": "一句话标题"}``(anchor 继承父问题)

    标题为空视为没声明 —— 空标题的问题在列表里就是一条噪声。
    """
    raw = outcome.get("next_question")
    if isinstance(raw, str):
        title = raw.strip()
        return {"title": title} if title else None
    if isinstance(raw, dict):
        title = str(raw.get("title") or "").strip()
        if not title:
            return None
        spec: dict[str, Any] = {"title": title}
        if raw.get("body"):
            spec["body"] = str(raw["body"])
        if isinstance(raw.get("anchor"), dict):
            spec["anchor"] = raw["anchor"]
        return spec
    if raw is not None:
        return None
    # 兜底:支柱的 next_proof_point 本来就是"下一块该验证什么"。
    fallback = (pillar_proof or "").strip()
    if fallback and not _BARE_DATE.match(fallback):
        return {"title": fallback, "body": "由支柱的 next_proof_point 派生。"}
    return None


async def _mission_question_id(db: Any, mission: dict[str, Any]) -> str | None:
    """归属解析由 ``mission_binding`` 统一 —— 见那里的注释为何不能各读各的。"""
    from app.core.mission_binding import mission_question_id

    return await mission_question_id(db, mission)


async def settle_mission(
    db: Any,
    mission: dict[str, Any],
    outcome: dict[str, Any],
    *,
    note: str | None = None,
    actor: str = "user",
) -> SettlementResult:
    """结案的四个产物(决策 + 子问题 + 复访),顺序固定。

    调用方在 ``transition(..., 'resolved')`` **之前**调用。本函数不抛异常:
    每一步的失败折进 ``warnings``,``ok`` 只反映"正式决策是否落账"。
    """
    mission_id = str(mission["id"])
    question_id = await _mission_question_id(db, mission)
    warnings: list[str] = []
    decision_id: str | None = None
    child_question_id: str | None = None
    revisit_at: str | None = None
    window_start: str | None = None
    window_end: str | None = None

    if not question_id:
        warnings.append(
            "mission 没有 context.question_id —— 结论只记在 mission 自身,"
            "未进 research_decisions(正式决策必须指向一个问题)"
        )
        question_id = None

    decided_at = _now()
    window_start, window_end = _resolve_window(outcome, decided_at)
    verdict = str(outcome.get("verdict") or "RESOLVED_BY_USER")
    rationale = str(outcome.get("rationale") or note or "").strip()

    if question_id:
        try:
            decision_id = await DecisionRepository(db).record(
                str(question_id),
                verdict,
                rationale=rationale,
                window_start=window_start,
                window_end=window_end,
            )
        except Exception as exc:  # noqa: BLE001 — 决策失败要显式上报,不静默
            logger.warning(
                "settle_mission: decision failed for %s", mission_id, exc_info=True
            )
            warnings.append(f"正式决策未落账:{type(exc).__name__}: {exc}"[:200])

    # 子问题:显式声明优先,否则退到 pillar 的 next_proof_point。
    pillar_proof: str | None = None
    try:
        pillars = await MissionThesisRepository(db).list_pillars(mission_id)
        # 取最后一个有 next_proof_point 的:支柱按创建序返回,越晚的越接近
        # 本轮验证后真正剩下的那块。
        for pillar in reversed(pillars):
            candidate = (pillar.get("next_proof_point") or "").strip()
            if candidate:
                pillar_proof = candidate
                break
    except Exception as exc:  # noqa: BLE001 — 读 pillar 失败只是少了兜底
        logger.warning(
            "settle_mission: pillar lookup failed for %s", mission_id, exc_info=True
        )

    spec = _next_question_spec(outcome, pillar_proof)
    if spec is not None and question_id:
        try:
            question_repo = ResearchQuestionRepository(db)
            parent = await question_repo.get(str(question_id))
            anchor = spec.get("anchor")
            inherited = False
            if anchor is None and parent is not None:
                anchor = parent.get("anchor") or {}
                inherited = bool(anchor)
            child_question_id = await question_repo.create(
                title=spec["title"],
                body=spec.get("body", ""),
                anchor=anchor,
                created_by="selene",
                parent_question_id=str(question_id),
            )
            await ResearchEdgeRepository(db).add_fact_edge(
                ("question", child_question_id),
                ("question", str(question_id)),
                "derived_from",
                actor="system",
            )
            if inherited:
                # 不猜标题里的"4h/ETH"该对应什么 anchor —— 那是编造。但要
                # 明说锚点是继承的:实测出现过标题写"扩大到 4h 与 ETH"、锚
                # 仍是 1h/BTC 的情况,静默继承会让下一轮跑错周期。
                inherited_anchor: dict[str, Any] = anchor or {}
                warnings.append(
                    "子问题未声明 anchor,已继承父问题的 "
                    f"{inherited_anchor.get('intervals') or '?'}"
                    f" @ {inherited_anchor.get('symbols') or '?'}"
                    " —— 若下一步要换周期/标的,请在结论的 next_question.anchor 里写明"
                )
        except Exception as exc:  # noqa: BLE001 — 子问题是增强,不连坐决策
            logger.warning(
                "settle_mission: child question failed for %s",
                mission_id,
                exc_info=True,
            )
            warnings.append(f"子问题未创建:{type(exc).__name__}: {exc}"[:200])
    elif spec is not None:
        warnings.append("派生问题需要父问题,但 mission 没有 question_id —— 已跳过")

    # 复访:结论有一个观察窗口,窗口结束后该回来看它成没成真。
    if question_id:
        try:
            end_dt = _parse_dt(window_end) or decided_at
            revisit_iso = _iso(end_dt + timedelta(days=REVISIT_AFTER_WINDOW_DAYS))
            parent_question = await ResearchQuestionRepository(db).get(str(question_id))
            # 只有 open/parked 接受复访计划;终态问题不需要被提醒。
            if parent_question is not None and parent_question.get("status") in (
                "open",
                "parked",
            ):
                await ResearchQuestionRepository(db).set_revisit(
                    str(question_id), revisit_iso, actor="system"
                )
                revisit_at = revisit_iso
            elif parent_question is not None:
                warnings.append(
                    f"原问题状态为 {parent_question.get('status')},不接受复访计划"
                )
        except Exception as exc:  # noqa: BLE001 — 复访同样是增强
            logger.warning(
                "settle_mission: revisit failed for %s", mission_id, exc_info=True
            )
            warnings.append(f"复访时点未设置:{type(exc).__name__}: {exc}"[:200])

    return SettlementResult(
        mission_id=mission_id,
        decision_id=decision_id,
        child_question_id=child_question_id,
        revisit_at=revisit_at,
        window_start=window_start,
        window_end=window_end,
        warnings=tuple(warnings),
    )


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "REVISIT_AFTER_WINDOW_DAYS",
    "SettlementResult",
    "settle_mission",
]

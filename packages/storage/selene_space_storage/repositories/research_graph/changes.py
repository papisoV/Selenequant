"""跨问题变化聚合 —— 主窗口 Research Control Surface 的数据源(§主窗口第一刀)。

四来源拼出「研究世界发生了什么变化」:
- events(research_question_events)—— 状态轴/调查进展,kind 分层
- revisions(research_revisions)—— 回答轴只增,**不落事件**,必须单查(F3)
- decisions(research_decisions)—— outcome_pending 是持续状态,常驻不过滤 since
- proposed questions(created_by='selene' + parent 非空)—— 待拍板,常驻

tier1(Requires Director)是**状态**不是事件:每次全量拉,不受 since 过滤 ——
上次访问前产生的待拍板提议若被 since 滤掉,就永远不会再出现,违反
"requires Director" 本义。tier2/3(判断性/进展性)是瞬态流,受 since 过滤。

CONSEQUENCE 全部为确定性模板,字段来自 DB 当前值 —— 这里不允许任何因果
脑补(「因为 X 所以 Y」),那是调查会的职责,不是变化流的职责。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from selene_space_storage.repositories.research_graph._shared import (
    _is_past,
    _load_json,
)

# 状态轴事件(判断性,tier2)与进展/记录事件(tier3)
_TIER2_EVENT_KINDS = ("closed", "reopened", "superseded", "superseding")
_TIER3_EVENT_KINDS = (
    "created",
    "forked",
    "investigate_perspective",
    "investigate_finished",
)

# tier1 内部的紧急序:观察窗到期 > 提议待拍板 > decision pending
_TIER1_RANK = {"window_expired": 0, "proposal": 1, "decision_pending": 2}

# 与端点 clamp 对齐;list_decisions 默认 limit=50 会静默漏报历史 decision
_DECISION_SCAN_LIMIT = 500


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _base(
    *,
    id: str,
    tier: int,
    occurred_at: str,
    source: str,
    kind: str,
    question_id: str | None,
    actor: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": id,
        "tier": tier,
        "occurred_at": occurred_at,
        "source": source,
        "kind": kind,
        "question_id": question_id,
        "actor": actor,
        "detail": detail,
        "epistemic": {
            "source": source,
            "actor": actor,
            "cites_evidence": False,
            "evidence_mode": None,
        },
    }


async def _load_question_map(db: Any) -> dict[str, dict[str, Any]]:
    """一次性全量拉 questions 建映射,禁逐条目回查(N+1)。单用户量级 <千行。"""
    rows = await db.execute(
        "SELECT id, title, status, parent_question_id FROM research_questions"
    )
    return {r["id"]: dict(r) for r in rows}


def _title_of(qmap: dict[str, dict[str, Any]], qid: str | None) -> str:
    if qid is None or qid not in qmap:
        return ""
    title = qmap[qid]["title"]
    return title if isinstance(title, str) else str(title)


def _parent_title_of(qmap: dict[str, dict[str, Any]], qid: str | None) -> str:
    if qid is None or qid not in qmap:
        return ""
    return _title_of(qmap, qmap[qid]["parent_question_id"])


async def _proposal_changes(
    db: Any, qmap: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """tier1:系统提议待拍板(created_by='selene' 且 parent 非空且仍 open)。"""
    rows = await db.execute(
        "SELECT id, title, parent_question_id, opened_at, created_by "
        "FROM research_questions WHERE created_by = 'selene' "
        "AND parent_question_id IS NOT NULL AND status = 'open'"
    )
    out = []
    for r in rows:
        change = _base(
            id=r["id"],
            tier=1,
            occurred_at=r["opened_at"],
            source="proposal",
            kind="proposal",
            question_id=r["id"],
            actor=r["created_by"],
            detail={"parent_question_id": r["parent_question_id"]},
        )
        parent_title = _parent_title_of(qmap, r["id"])
        change["consequence"] = (
            f"待拍板新问题「{r['title']}」(承接「{parent_title}」)"
            if parent_title
            else f"待拍板新问题「{r['title']}」"
        )
        out.append(change)
    return out


async def _decision_changes(
    db: Any, qmap: dict[str, dict[str, Any]], now: str
) -> list[dict[str, Any]]:
    """tier1:outcome_pending 且(无观察窗 或 观察窗已到期)。"""
    rows = await db.execute(
        "SELECT id, question_id, verdict, outcome_status, window_end, decided_at "
        "FROM research_decisions WHERE outcome_status = 'outcome_pending' "
        "ORDER BY decided_at DESC LIMIT ?",
        (_DECISION_SCAN_LIMIT,),
    )
    out = []
    for r in rows:
        expired = _is_past(r["window_end"], now)
        if r["window_end"] is not None and not expired:
            continue  # 观察窗未到期,不催
        kind = "window_expired" if expired else "decision_pending"
        change = _base(
            id=r["id"],
            tier=1,
            occurred_at=r["decided_at"],
            source="decision",
            kind=kind,
            question_id=r["question_id"],
            actor="director",
            detail={"verdict": r["verdict"], "window_end": r["window_end"]},
        )
        title = _title_of(qmap, r["question_id"])
        if expired:
            change["consequence"] = (
                f"decision {r['id']} 观察窗已于 {r['window_end']} 到期, outcome 仍 pending"
            )
        else:
            change["consequence"] = f"decision {r['id']} 已拍板, outcome 待记录"
        change["question_title"] = title
        out.append(change)
    return out


async def _event_changes(
    db: Any,
    qmap: dict[str, dict[str, Any]],
    *,
    kinds: tuple[str, ...],
    tier: int,
    since: str | None,
) -> list[dict[str, Any]]:
    placeholders = ", ".join("?" for _ in kinds)
    sql = (
        f"SELECT id, question_id, occurred_at, actor, kind, detail_json "
        f"FROM research_question_events WHERE kind IN ({placeholders})"
    )
    params: list[Any] = list(kinds)
    if since is not None:
        sql += " AND occurred_at > ?"
        params.append(since)
    rows = await db.execute(sql, tuple(params))
    out = []
    for r in rows:
        detail = _load_json(r["detail_json"]) or {}
        change = _base(
            id=f"event-{r['id']}",
            tier=tier,
            occurred_at=r["occurred_at"],
            source="event",
            kind=r["kind"],
            question_id=r["question_id"],
            actor=r["actor"],
            detail=detail,
        )
        title = _title_of(qmap, r["question_id"])
        if r["kind"] in _TIER2_EVENT_KINDS:
            to_status = detail.get("to") or (
                qmap.get(r["question_id"], {}).get("status")
                if r["question_id"]
                else None
            )
            change["consequence"] = f"「{title}」当前状态 {to_status}"
        else:
            change["consequence"] = f"「{title}」{r['kind']}"
        out.append(change)
    return out


async def _revision_changes(
    db: Any,
    qmap: dict[str, dict[str, Any]],
    *,
    since: str | None,
) -> list[dict[str, Any]]:
    """回答轴不落事件(F3),revision 变化必须单查。"""
    sql = "SELECT id, question_id, seq, cited_json, actor, created_at FROM research_revisions"
    params: list[Any] = []
    if since is not None:
        sql += " WHERE created_at > ?"
        params.append(since)
    rows = await db.execute(sql, tuple(params))
    counts = {
        r["question_id"]: r["n"]
        for r in await db.execute(
            "SELECT question_id, COUNT(*) AS n FROM research_revisions GROUP BY question_id"
        )
    }
    out = []
    for r in rows:
        cited = _load_json(r["cited_json"]) or []
        change = _base(
            id=r["id"],
            tier=2,
            occurred_at=r["created_at"],
            source="revision",
            kind="revision",
            question_id=r["question_id"],
            actor=r["actor"],
            detail={"seq": r["seq"]},
        )
        change["epistemic"]["cites_evidence"] = any(
            str(c).startswith(("ev-", "ep-")) for c in cited
        )
        title = _title_of(qmap, r["question_id"])
        change["consequence"] = (
            f"「{title}」当前回答 v{r['seq']}(共 {counts.get(r['question_id'], 0)} 版)"
        )
        out.append(change)
    return out


async def _evidence_changes(
    db: Any,
    qmap: dict[str, dict[str, Any]],
    *,
    since: str | None,
) -> list[dict[str, Any]]:
    """新 evidence 落地:必须经 addresses 边连进某个问题 —— 无连边的
    evidence 不进任何 question 的研究世界,也就不进任何问题的变化流。"""
    sql = (
        "SELECT e.id, e.mode, e.created_at, addr.to_id AS question_id "
        "FROM research_evidence e "
        "JOIN research_edges addr ON addr.from_type = 'evidence' "
        "AND addr.from_id = e.id AND addr.kind = 'addresses' "
        "AND addr.to_type = 'question'"
    )
    params: list[Any] = []
    if since is not None:
        sql += " WHERE e.created_at > ?"
        params.append(since)
    rows = await db.execute(sql, tuple(params))
    out = []
    for r in rows:
        change = _base(
            id=r["id"],
            tier=3,
            occurred_at=r["created_at"],
            source="evidence",
            kind="evidence",
            question_id=r["question_id"],
            actor="selene",
            detail={"mode": r["mode"]},
        )
        change["epistemic"]["evidence_mode"] = r["mode"]
        title = _title_of(qmap, r["question_id"])
        change["consequence"] = f"「{title}」新增证据 {r['mode']}"
        out.append(change)
    return out


async def collect_changes(
    db: Any,
    *,
    since: str | None = None,
    limit: int = 200,
    now: str | None = None,
) -> dict[str, Any]:
    """聚合四来源为 State Change 流。tier1 常驻,tier2/3 受 since 过滤。

    limit 在 merge+排序**之后**裁剪(tier ASC 稳定排序保证 tier1 不被
    tier3 洪泛挤出)。counts 反映裁剪前的全量分布。
    """
    now = now or _utcnow()
    qmap = await _load_question_map(db)

    t1 = await _proposal_changes(db, qmap)
    t1.extend(await _decision_changes(db, qmap, now))
    t2 = await _event_changes(db, qmap, kinds=_TIER2_EVENT_KINDS, tier=2, since=since)
    t2.extend(await _revision_changes(db, qmap, since=since))
    t3 = await _event_changes(db, qmap, kinds=_TIER3_EVENT_KINDS, tier=3, since=since)
    t3.extend(await _evidence_changes(db, qmap, since=since))

    changes = t1 + t2 + t3
    for change in changes:
        q = qmap.get(change["question_id"]) if change["question_id"] else None
        change["question_title"] = _title_of(qmap, change["question_id"])
        change["question_status"] = q["status"] if q else None
        change["needs_director"] = change["tier"] == 1

    # tier1 内:观察窗到期(最急) > 提议 > decision pending;
    # 同 rank 内时间倒序 —— 先按时间升序排,再按 rank 降序做稳定排序
    t1.sort(key=lambda c: c["occurred_at"], reverse=True)
    t1.sort(key=lambda c: _TIER1_RANK[c["kind"]])

    t2.sort(key=lambda c: c["occurred_at"], reverse=True)
    t3.sort(key=lambda c: c["occurred_at"], reverse=True)

    ordered = t1 + t2 + t3
    return {
        "changes": ordered[:limit],
        "counts": {"t1": len(t1), "t2": len(t2), "t3": len(t3)},
    }

"""Mission thesis repository — falsifiable pillars + append-only evidence ledger.

认识论纪律(external-research-project thesis-tracker 转译,理念非代码):
- 证据账本 append-only:evidence 无 UPDATE/DELETE 方法;更正 = 追加新行。
- pillar 实体字段(claim/priority/baseline)禁改;变更 = 新 pillar 行 +
  旧行 ``superseded_by`` 处置指针。快照列(latest_evidence_id/signal)是
  纯缓存,每次 append_evidence 按账本派生基线对账推进。
- 聚合读分布不读快照:``aggregate_status`` 是纯函数,吃 pillar 状态分布。

activity kind 形状契约(detail_json 必含键):
- ``pillar_opened``:        {pillar_id, claim, priority}
- ``evidence_appended``:    {evidence_id, pillar_id, signal, fact_excerpt}
- ``status_set``:           {pillar_id, from, to, note?}
- ``pillar_superseded``:    {old_id, new_id, reason}
- ``drift_check``:          {patterns: {<DRIFT_PATTERNS 五键各 {present, note?}>}, checked_by, note?}
- ``drift_hint``:           {pillar_id, overdue_days}
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from selene_space_storage.repositories._mission_thesis_vocab import (
    DRIFT_PATTERNS,
    PILLAR_PRIORITIES,
    PILLAR_STATUSES,
    PROVENANCE_LEVELS,
    SIGNALS,
)

ACT_PILLAR_OPENED = "pillar_opened"
ACT_EVIDENCE_APPENDED = "evidence_appended"
ACT_STATUS_SET = "status_set"
ACT_PILLAR_SUPERSEDED = "pillar_superseded"
ACT_DRIFT_CHECK = "drift_check"
ACT_DRIFT_HINT = "drift_hint"

# 聚合严重度全序(watch 以下 benign 档同列;strengthening/intact tie 取 intact,
# 更保守,防 "strengthening" 叙事抬高聚合)。此全序为 selene 形式化,非
# external-research-project 直译(原 skill 只给三条规则,无全序)。
_SEVERITY_ORDER: dict[str, int] = {
    "broken": 6,
    "changed": 5,
    "impaired": 4,
    "watch": 3,
    "intact": 2,
    "strengthening": 2,
    "untested": 1,
    "retired": 0,
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _dump_json(value: Any) -> str:
    if value is None or value == {}:
        return "{}" if isinstance(value, dict) else "null"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_json(raw: str | None) -> Any:
    if not raw:
        return None
    return json.loads(raw)


def _fact_excerpt(fact: str, limit: int = 80) -> str:
    return fact if len(fact) <= limit else fact[: limit - 1] + "…"


def aggregate_status(pillars: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate pillar statuses into one mission-level verdict.

    规则(external-research-project reconciliation 直译 + selene 形式化标注):
    1. 任一活跃 core pillar 为 broken/changed → aggregate 同值。
    2. ≥2 个活跃 core pillar 为 impaired → aggregate = impaired。
    3. 1 个活跃 core pillar 为 impaired 且其余推出的聚合更 benign →
       aggregate = watch + requires_override=True(写入面归后续小件,v0 只读)。
    4. 否则 = 活跃 pillar 中最重档(序见 _SEVERITY_ORDER;全 untested →
       untested;strengthening/intact tie 取 intact)。

    活跃 = superseded_by IS NULL 且 status != 'retired'。
    """
    active = [
        p
        for p in pillars
        if p.get("superseded_by") is None and p.get("status") != "retired"
    ]
    core = [p for p in active if p.get("priority") == "core"]
    core_statuses = [p["status"] for p in core]

    for hard in ("broken", "changed"):
        if hard in core_statuses:
            return {"aggregate": hard, "requires_override": False}

    impaired_core = core_statuses.count("impaired")
    if impaired_core >= 2:
        return {"aggregate": "impaired", "requires_override": False}

    statuses = [p["status"] for p in active]
    if not statuses or all(s == "untested" for s in statuses):
        aggregate = "untested"
    else:
        aggregate = max(statuses, key=lambda s: (_SEVERITY_ORDER[s], s == "intact"))

    if impaired_core == 1:
        # 规则3:仅剩 1 个 impaired core,且其余活跃 pillar 全部更 benign →
        # watch + 人工复核旗标(其余含 supporting impaired 则不触发)。
        others = [
            p["status"]
            for p in active
            if not (p.get("priority") == "core" and p["status"] == "impaired")
        ]
        if all(_SEVERITY_ORDER[s] < _SEVERITY_ORDER["impaired"] for s in others):
            return {"aggregate": "watch", "requires_override": True}

    return {"aggregate": aggregate, "requires_override": False}


class MissionThesisRepository:
    """Facade over ``mission_pillars`` + ``mission_evidence``."""

    def __init__(self, db: Any) -> None:
        self._db = db

    # -- pillars ---------------------------------------------------------------

    async def add_pillar(
        self,
        mission_id: str,
        claim: str,
        priority: str,
        *,
        baseline: str | None = None,
        expected_path: str | None = None,
        confirm_threshold: str | None = None,
        warning_threshold: str | None = None,
        break_threshold: str | None = None,
        threshold_provenance: str = "draft",
        provenance: str = "draft",
        model_line: str | None = None,
        implied_action: str | None = None,
        next_proof_point: str | None = None,
        next_proof_source: str | None = None,
        owner: str = "selene",
    ) -> str:
        """Open a falsifiable pillar. Returns ``pil-<hex12>``."""
        if not claim or not claim.strip():
            raise ValueError("pillar claim must be a non-empty falsifiable statement")
        if priority not in PILLAR_PRIORITIES:
            raise ValueError(
                f"unknown priority: {priority!r} (expected {PILLAR_PRIORITIES})"
            )
        if threshold_provenance not in PROVENANCE_LEVELS:
            raise ValueError(
                f"unknown threshold_provenance: {threshold_provenance!r} "
                f"(expected one of {PROVENANCE_LEVELS} — 这是置信等级,不是来源描述)"
            )
        if provenance not in PROVENANCE_LEVELS:
            raise ValueError(
                f"unknown provenance: {provenance!r} "
                f"(expected one of {PROVENANCE_LEVELS} — 这是置信等级;"
                f"来源描述请填 model_line)"
            )
        mission = await self._db.execute(
            "SELECT id FROM research_missions WHERE id = ?", (mission_id,)
        )
        if not mission:
            raise KeyError(f"mission not found: {mission_id}")
        pillar_id = f"pil-{uuid.uuid4().hex[:12]}"
        now = _now()
        await self._db.execute(
            """
            INSERT INTO mission_pillars (
                id, mission_id, claim, priority, baseline, expected_path,
                confirm_threshold, warning_threshold, break_threshold,
                threshold_provenance, provenance, model_line, implied_action,
                next_proof_point, next_proof_source, owner, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pillar_id,
                mission_id,
                claim.strip(),
                priority,
                baseline,
                expected_path,
                confirm_threshold,
                warning_threshold,
                break_threshold,
                threshold_provenance,
                provenance,
                model_line,
                implied_action,
                next_proof_point,
                next_proof_source,
                owner,
                now,
                now,
            ),
        )
        await self._append_activity(
            mission_id,
            actor=owner,
            kind=ACT_PILLAR_OPENED,
            detail={
                "pillar_id": pillar_id,
                "claim": claim.strip(),
                "priority": priority,
            },
        )
        return pillar_id

    async def get_pillar(self, pillar_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM mission_pillars WHERE id = ?", (pillar_id,)
        )
        return dict(rows[0]) if rows else None

    async def list_pillars(self, mission_id: str) -> list[dict[str, Any]]:
        rows = await self._db.execute(
            """
            SELECT * FROM mission_pillars WHERE mission_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (mission_id,),
        )
        return [dict(r) for r in rows]

    async def set_pillar_status(
        self,
        pillar_id: str,
        status: str,
        *,
        status_note: str | None = None,
        actor: str = "selene",
    ) -> dict[str, Any]:
        """Move a pillar to a new status (snapshot column; history in activity)."""
        if status not in PILLAR_STATUSES:
            raise ValueError(
                f"unknown pillar status: {status!r} (expected {PILLAR_STATUSES})"
            )
        pillar = await self.get_pillar(pillar_id)
        if pillar is None:
            raise KeyError(f"pillar not found: {pillar_id}")
        old_status = pillar["status"]
        now = _now()
        # Guarded update: WHERE carries the status we read so a concurrent
        # status change is a no-op — the activity row must never record a
        # "from" value that was already stale when written.
        await self._db.execute(
            "UPDATE mission_pillars SET status = ?, status_note = ?, updated_at = ? "
            "WHERE id = ? AND status = ?",
            (status, status_note, now, pillar_id, old_status),
        )
        updated = await self.get_pillar(pillar_id)
        assert updated is not None
        if updated["status"] != status:
            raise ValueError(
                f"pillar status changed concurrently for {pillar_id}: "
                f"expected {old_status!r}, actual {updated['status']!r}"
            )
        detail: dict[str, Any] = {
            "pillar_id": pillar_id,
            "from": old_status,
            "to": status,
        }
        if status_note:
            detail["note"] = status_note
        await self._append_activity(
            pillar["mission_id"], actor=actor, kind=ACT_STATUS_SET, detail=detail
        )
        updated = await self.get_pillar(pillar_id)
        assert updated is not None
        return updated

    async def supersede_pillar(self, old_id: str, new_id: str, *, reason: str) -> None:
        """Mark old pillar superseded by its replacement row. claim 禁改路径。

        repo 级能力(HTTP 面归后续小件):旧行写 superseded_by 指针,实体字段不动。
        """
        old = await self.get_pillar(old_id)
        if old is None:
            raise KeyError(f"pillar not found: {old_id}")
        new = await self.get_pillar(new_id)
        if new is None:
            raise KeyError(f"pillar not found: {new_id}")
        now = _now()
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE mission_pillars SET superseded_by = ?, updated_at = ? WHERE id = ?",
                (new_id, now, old_id),
            )
            await conn.execute(
                """
                INSERT INTO mission_activity (mission_id, occurred_at, actor, kind, detail_json)
                VALUES (?, ?, 'selene', ?, ?)
                """,
                (
                    old["mission_id"],
                    now,
                    ACT_PILLAR_SUPERSEDED,
                    _dump_json({"old_id": old_id, "new_id": new_id, "reason": reason}),
                ),
            )

    # -- evidence ledger (append-only) ------------------------------------------

    async def append_evidence(
        self,
        mission_id: str,
        pillar_id: str,
        *,
        occurred_at: str,
        source_type: str,
        fact: str,
        interpretation: str,
        signal: str,
        source_ref: str | None = None,
        prior_expectation: str | None = None,
        market_expectation: str | None = None,
        evidence_quality: str | None = None,
        impact: dict[str, Any] | None = None,
        follow_up: str | None = None,
        owner: str = "selene",
    ) -> str:
        """唯一写入口:三写单事务(evidence INSERT + pillar 快照对账 + activity 指针)。

        快照推进规则:仅当新行 ``(occurred_at, seq)`` tuple 严格大于账本派生
        基线 ``MAX(occurred_at, seq)``(按 pillar_id 从 mission_evidence 查询,
        不读快照列自身)时推进 ``latest_evidence_id``/``signal``;否则只入账
        不动快照(回填旧日期行天然不覆盖)。快照恒为账本派生缓存,每次
        append 幂等对账,分叉不可累积。

        Returns:
            The generated evidence id (``ev-<hex12>``).
        """
        if signal not in SIGNALS:
            raise ValueError(f"unknown signal: {signal!r} (expected {SIGNALS})")
        if not fact or not fact.strip():
            raise ValueError("evidence fact must be non-empty")
        if not interpretation or not interpretation.strip():
            raise ValueError("evidence interpretation must be non-empty")
        pillar = await self.get_pillar(pillar_id)
        if pillar is None:
            raise KeyError(f"pillar not found: {pillar_id}")
        if pillar["mission_id"] != mission_id:
            raise ValueError(
                f"pillar {pillar_id} does not belong to mission {mission_id}"
            )
        evidence_id = f"ev-{uuid.uuid4().hex[:12]}"
        now = _now()
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO mission_evidence (
                    id, mission_id, pillar_id, occurred_at, source_type, source_ref,
                    fact, prior_expectation, market_expectation, interpretation,
                    signal, evidence_quality, impact_json, follow_up, owner, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    mission_id,
                    pillar_id,
                    occurred_at,
                    source_type,
                    source_ref,
                    fact.strip(),
                    prior_expectation,
                    market_expectation,
                    interpretation.strip(),
                    signal,
                    evidence_quality,
                    _dump_json(impact or {}),
                    follow_up,
                    owner,
                    now,
                ),
            )
            new_seq = cursor.lastrowid or -1
            # 账本派生基线:排除本行后的 MAX(occurred_at, seq),tuple 严格大于才推进。
            baseline_rows = await conn.execute(
                """
                SELECT occurred_at, seq FROM mission_evidence
                WHERE pillar_id = ? AND seq != ?
                ORDER BY occurred_at DESC, seq DESC LIMIT 1
                """,
                (pillar_id, new_seq),
            )
            baseline = await baseline_rows.fetchone()
            if baseline is None or (occurred_at, new_seq) > (
                baseline["occurred_at"],
                baseline["seq"],
            ):
                await conn.execute(
                    """
                    UPDATE mission_pillars
                    SET latest_evidence_id = ?, signal = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (evidence_id, signal, now, pillar_id),
                )
            await conn.execute(
                """
                INSERT INTO mission_activity (mission_id, occurred_at, actor, kind, detail_json)
                VALUES (?, ?, 'selene', ?, ?)
                """,
                (
                    mission_id,
                    now,
                    ACT_EVIDENCE_APPENDED,
                    _dump_json(
                        {
                            "evidence_id": evidence_id,
                            "pillar_id": pillar_id,
                            "signal": signal,
                            "fact_excerpt": _fact_excerpt(fact.strip()),
                        }
                    ),
                ),
            )
        return evidence_id

    async def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM mission_evidence WHERE id = ?", (evidence_id,)
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["impact"] = _load_json(row.pop("impact_json", None))
        return row

    async def list_evidence(
        self,
        mission_id: str,
        pillar_id: str | None = None,
        signal: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Oldest-first ledger replay, ordered by (occurred_at, seq)."""
        if signal is not None and signal not in SIGNALS:
            raise ValueError(f"unknown signal: {signal!r}")
        clauses = ["mission_id = ?"]
        params: list[Any] = [mission_id]
        if pillar_id is not None:
            clauses.append("pillar_id = ?")
            params.append(pillar_id)
        if signal is not None:
            clauses.append("signal = ?")
            params.append(signal)
        params.append(limit)
        rows = await self._db.execute(
            f"""
            SELECT * FROM mission_evidence WHERE {' AND '.join(clauses)}
            ORDER BY occurred_at ASC, seq ASC LIMIT ?
            """,
            tuple(params),
        )
        result = []
        for r in rows:
            row = dict(r)
            row["impact"] = _load_json(row.pop("impact_json", None))
            result.append(row)
        return result

    # -- drift -----------------------------------------------------------------

    async def record_drift_check(
        self,
        mission_id: str,
        patterns: dict[str, dict[str, Any]],
        *,
        checked_by: str = "selene",
        note: str | None = None,
    ) -> None:
        """Record one five-pattern drift annotation (append-only activity row)."""
        missing = [k for k in DRIFT_PATTERNS if k not in patterns]
        if missing:
            raise ValueError(f"drift check missing patterns: {missing}")
        normalized: dict[str, dict[str, Any]] = {}
        for key in DRIFT_PATTERNS:
            entry = patterns[key]
            if not isinstance(entry, dict) or "present" not in entry:
                raise ValueError(
                    f"drift pattern {key!r} must be an object with 'present'"
                )
            normalized[key] = {
                "present": bool(entry["present"]),
                **({"note": entry["note"]} if entry.get("note") else {}),
            }
        detail: dict[str, Any] = {"patterns": normalized, "checked_by": checked_by}
        if note:
            detail["note"] = note
        await self._append_activity(
            mission_id, actor="selene", kind=ACT_DRIFT_CHECK, detail=detail
        )

    async def detect_stale_proof_points(
        self, mission_id: str, grace_days: int = 7, now: str | None = None
    ) -> list[dict[str, Any]]:
        """Rule hint: overdue next_proof_point without post-date evidence.

        now 可注入(ISO 日期)以获得确定性断言;默认当前 UTC 日期。

        .. deprecated:: 2026-10-02
            **不要在生产路径调用**(含只读 GET):本方法每次命中都会 append
            ``drift_hint`` activity,放进 GET 会污染 append-only 账本。

            且其判定有已知缺陷:把 ``next_proof_point[:10]`` 当 ISO 日期做
            字符串比较,而该字段实际是自由文本。实测真库值
            ``'funding_rates 补到最新后重跑'`` 的切片 ``'funding_ra'``
            字典序 >= 任何 ``'2026-xx-xx'`` → 恒被 ``continue`` 静默跳过,
            永不触发,无任何报错。

            截止日若要机读,应走独立的结构化字段 + 显式格式校验
            (见 docs/survey-mirofish-bettafish-round2.md N1);
            不要在此方法上叠加语义。当前仅测试(``test_mission_thesis.py``)
            引用它,保留以锁定上述历史行为。
        """
        ref_date = (now or datetime.now(UTC).isoformat(timespec="seconds"))[:10]
        pillars = await self.list_pillars(mission_id)
        hits: list[dict[str, Any]] = []
        for p in pillars:
            if p.get("superseded_by") is not None or p["status"] == "retired":
                continue
            proof_point = p.get("next_proof_point")
            if not proof_point or proof_point[:10] >= ref_date:
                continue
            grace = (
                datetime.now(UTC)
                if now is None
                else datetime.fromisoformat(ref_date + "T00:00:00+00:00")
            )
            overdue_days = (
                grace - datetime.fromisoformat(proof_point[:10] + "T00:00:00+00:00")
            ).days
            if overdue_days < grace_days:
                continue
            later = await self._db.execute(
                """
                SELECT COUNT(*) AS n FROM mission_evidence
                WHERE pillar_id = ? AND occurred_at > ?
                """,
                (p["id"], proof_point),
            )
            row = later[0]
            if row["n"] > 0:
                continue
            hits.append({"pillar_id": p["id"], "overdue_days": overdue_days})
            await self._append_activity(
                mission_id,
                actor="selene",
                kind=ACT_DRIFT_HINT,
                detail={"pillar_id": p["id"], "overdue_days": overdue_days},
            )
        return hits

    # -- activity ---------------------------------------------------------------

    async def _append_activity(
        self,
        mission_id: str,
        *,
        actor: str,
        kind: str,
        detail: dict[str, Any] | None = None,
    ) -> int:
        if actor not in ("selene", "user", "system"):
            raise ValueError(f"unknown actor: {actor!r}")
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO mission_activity (mission_id, occurred_at, actor, kind, detail_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (mission_id, _now(), actor, kind, _dump_json(detail or {})),
            )
            await conn.execute(
                "UPDATE research_missions SET updated_at = ? WHERE id = ?",
                (_now(), mission_id),
            )
        return cursor.lastrowid or -1

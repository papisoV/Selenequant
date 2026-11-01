"""Research missions — B1 state machine + B2 activity stream repository.

B1: Mission 生命周期状态机。phase 转移受合法性表约束(非法边直接 ValueError),
    防前端/脚本把 Mission 推进到不存在的工作流。
B2: mission_activity append-only 活动流。每次状态转移、用户动作、Selene 工作
    都追加一行;读侧按时间正序回放。绝不 UPDATE 既有行。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

# market 主体的**半门**形态:SYMBOL 或 SYMBOL/QUOTE(如 BTC、BTC/USDT、1000PEPE/USDT)。
# 真实标的校验(是否在交易所上市)留 follow-up —— 这里只挡"明显不是标的"的自由文本
# (含空格/空串/整句)。见 ``_resolve_subject_id`` 的设计说明。
_MARKET_SUBJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")

# ---------------------------------------------------------------------------
# B1 state machine
# ---------------------------------------------------------------------------

PHASE_INVESTIGATION = "investigation"
PHASE_VALIDATION = "validation"
PHASE_NEEDS_INPUT = "needs_input"
PHASE_RESOLVED = "resolved"
PHASE_PARKED = "parked"

PHASES = (
    PHASE_INVESTIGATION,
    PHASE_VALIDATION,
    PHASE_NEEDS_INPUT,
    PHASE_RESOLVED,
    PHASE_PARKED,
)

# 合法状态转移边(NEXT 四动作词表 v0:approve→validation 收口、redirect→investigation
# 重新定向、park→parked 停放、needs_input 反向门)。selene 完成自主调查时从
# investigation/validation 推到 needs_input 等待负责人。
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    PHASE_INVESTIGATION: frozenset({PHASE_VALIDATION, PHASE_NEEDS_INPUT, PHASE_PARKED}),
    PHASE_VALIDATION: frozenset({PHASE_NEEDS_INPUT, PHASE_PARKED, PHASE_RESOLVED}),
    PHASE_NEEDS_INPUT: frozenset({PHASE_INVESTIGATION, PHASE_VALIDATION, PHASE_PARKED}),
    PHASE_RESOLVED: frozenset(),
    PHASE_PARKED: frozenset({PHASE_INVESTIGATION, PHASE_VALIDATION}),
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


#: How long a worker claim survives before another consumer may take over.
#: Comfortably longer than one LLM round (the 2026-10-21 rounds took 4–30 s),
#: short enough that a crashed worker costs one interval, not the mission.
CLAIM_TTL_SECONDS = 15 * 60


def _claim_until(now: str) -> str:
    """Expiry stamp for a claim taken at ``now``.

    Kept next to the claim itself so the guarded UPDATE stays a single
    statement; a malformed ``now`` must not silently grant an eternal claim.
    """
    try:
        started = datetime.fromisoformat(now)
    except ValueError:
        started = datetime.now(UTC)
    return (started + timedelta(seconds=CLAIM_TTL_SECONDS)).isoformat(
        timespec="seconds"
    )


class ResearchMissionRepository:
    """Facade over ``research_missions`` + ``mission_activity``."""

    def __init__(self, db: Any) -> None:
        self._db = db

    # -- B1: mission lifecycle ------------------------------------------------

    async def _validate_subject_id(self, subject_type: str, subject_id: str) -> None:
        """主体 id 存在性门(按 ``subject_type`` 分级)。

        - ``strategy`` = **硬门**:``subject_id`` 必须命中 ``strategies.id``。
          这是 2026-10-13 的承重修复 —— 探针名(``entry-delay-ic-probe``)/
          回测 id(``bt-...``)此前能静默成为 strategy 主体,让驾驶舱 State
          端点 404 却把业务"查无此策略"错报成"确认 local-api 已启动"。
        - ``market`` = **半门**:只挡明显不是标的的自由文本(空串/含空格/
          整句)。真实"该标的是否上市"留 follow-up(需交易所清单,本层没有,
          且 anchor 侧本就混用 ``BTC`` 与 ``BTC/USDT`` 两种写法)。
        - ``event`` / ``system`` = 显式自由:无存在性判据,直接放行。

        ``strategies`` 表缺失(未经 v001 迁移的库)时**放行** —— 门是防脏
        数据,不该反过来把无关路径打死;兜底是审计规则 11 的事后检出。

        Raises:
            ValueError: 主体不合法 —— 两个 API 层都把它映射成 4xx。
        """
        if subject_type == "strategy":
            try:
                rows = await self._db.execute(
                    "SELECT 1 FROM strategies WHERE id = ? LIMIT 1", (subject_id,)
                )
            except Exception:  # noqa: BLE001 — 表缺失 = 不可校验,放行(见 docstring)
                return
            if not rows:
                raise ValueError(
                    f"subject_id {subject_id!r} 不是任何已注册策略 —— "
                    "策略主体必须指向 strategies.id(如 strat-xxxx);"
                    "若这是探针/回测/自由课题,请改用 event 或 system 主体"
                )
        elif subject_type == "market":
            candidate = (subject_id or "").strip()
            if not candidate or not _MARKET_SUBJECT_RE.match(candidate):
                raise ValueError(
                    f"market subject_id {subject_id!r} 不是合法标的形态"
                    "(应为 SYMBOL 或 SYMBOL/QUOTE,如 BTC/USDT)"
                )

    async def create(
        self,
        title: str,
        question: str,
        subject_type: str,
        subject_id: str,
        created_by: str = "selene",
        context: dict[str, Any] | None = None,
        dedup: bool = True,
    ) -> str:
        """Open a new mission in ``investigation`` phase.

        ``dedup``(默认 True):若同一 (subject_type, subject_id, question) 已存在
        未 resolved/parked 的 mission,直接返回既有 id 而不新建。
        唯一性不变量下沉到这里,任何调用方都受保护(此前仅 pa_mission_sensor
        自查,绕过它的路径产生了重复 mission — 2026-10-01 实测 4 条同题)。

        Returns:
            The generated (or reused) mission id (``mis-<hex12>``).
        """
        if subject_type not in ("strategy", "market", "event", "system"):
            raise ValueError(f"unknown subject_type: {subject_type!r}")
        await self._validate_subject_id(subject_type, subject_id)
        if dedup:
            existing = await self.find_open_by_question(
                subject_type, subject_id, question
            )
            if existing is not None:
                return str(existing["id"])
        mission_id = f"mis-{uuid.uuid4().hex[:12]}"
        now = _now()
        await self._db.execute(
            """
            INSERT INTO research_missions (
                id, title, question, subject_type, subject_id,
                phase, stage, created_by, opened_at, updated_at, context_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                title,
                question,
                subject_type,
                subject_id,
                PHASE_INVESTIGATION,
                "opened",
                created_by,
                now,
                now,
                _dump_json(context or {}),
            ),
        )
        await self.append_activity(
            mission_id,
            actor="system",
            kind="mission_opened",
            detail={"title": title, "question": question, "subject_id": subject_id},
        )
        return mission_id

    async def get(self, mission_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM research_missions WHERE id = ?", (mission_id,)
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["context"] = _load_json(row.pop("context_json", None))
        row["outcome"] = _load_json(row.pop("outcome_json", None))
        return row

    async def list_missions(
        self,
        subject_type: str | None = None,
        subject_id: str | None = None,
        phase: str | None = None,
        limit: int = 50,
        exclude_archived_duplicates: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if subject_type is not None:
            clauses.append("subject_type = ?")
            params.append(subject_type)
        if subject_id is not None:
            clauses.append("subject_id = ?")
            params.append(subject_id)
        if phase is not None:
            clauses.append("phase = ?")
            params.append(phase)
        if exclude_archived_duplicates:
            # 维护性归档(同一问句的重复开案被转 parked)不是研究面上的"世界变化"。
            # 过滤必须下沉到 SQL:27 条归档一次写入会把 parked 的 LIMIT 窗口整个
            # 占满,先取再筛会让 WORLD CHANGED / LAST HANDOFF 又变回空。
            clauses.append(
                "json_extract(context_json, '$.archive.duplicate_of') IS NULL"
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = await self._db.execute(
            f"SELECT * FROM research_missions {where} ORDER BY updated_at DESC LIMIT ?",
            tuple(params),
        )
        result = []
        for r in rows:
            row = dict(r)
            row["context"] = _load_json(row.pop("context_json", None))
            row["outcome"] = _load_json(row.pop("outcome_json", None))
            result.append(row)
        return result

    async def transition(
        self,
        mission_id: str,
        to_phase: str,
        *,
        actor: str = "selene",
        note: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Move a mission along the state machine; rejects illegal edges.

        The UPDATE carries a phase guard (``WHERE ... AND phase = ?``) so a
        concurrent transition between our read and write cannot silently
        overwrite an illegal jump — guarded transition, not read-then-write.

        Returns:
            The updated mission row.
        """
        mission = await self.get(mission_id)
        if mission is None:
            raise KeyError(f"mission not found: {mission_id}")
        from_phase = mission["phase"]
        if to_phase not in PHASES:
            raise ValueError(f"unknown phase: {to_phase!r}")
        if to_phase not in LEGAL_TRANSITIONS[from_phase]:
            raise ValueError(f"illegal transition {from_phase} -> {to_phase}")
        now = _now()
        # execute() returns rows, not rowcount — verify the guard's effect by
        # re-reading the row below.
        await self._db.execute(
            """
            UPDATE research_missions
            SET phase = ?, updated_at = ?,
                resolved_at = CASE WHEN ? = 'resolved' THEN ? ELSE resolved_at END
            WHERE id = ? AND phase = ?
            """,
            (to_phase, now, to_phase, now, mission_id, from_phase),
        )
        updated = await self.get(mission_id)
        assert updated is not None
        if updated["phase"] != to_phase:
            # Lost the race: another transition moved the phase between our
            # read and write; the guarded UPDATE was a no-op.
            await self.append_activity(
                mission_id,
                actor=actor,
                kind="phase_transition_conflict",
                detail={
                    "attempted_from": from_phase,
                    "attempted_to": to_phase,
                    "actual": updated["phase"],
                },
            )
            raise ValueError(
                f"phase changed concurrently for {mission_id}: "
                f"expected {from_phase!r}, actual {updated['phase']!r}"
            )
        activity: dict[str, Any] = {"from": from_phase, "to": to_phase}
        if note:
            activity["note"] = note
        if detail:
            activity.update(detail)
        await self.append_activity(
            mission_id, actor=actor, kind="phase_transition", detail=activity
        )
        updated = await self.get(mission_id)
        assert updated is not None
        return updated

    async def update_context(
        self, mission_id: str, context: dict[str, Any], *, merge: bool = True
    ) -> None:
        """Replace (merge=False) or merge into (merge=True) mission context."""
        mission = await self.get(mission_id)
        if mission is None:
            raise KeyError(f"mission not found: {mission_id}")
        if merge and isinstance(mission["context"], dict):
            merged = {**mission["context"], **context}
        else:
            merged = context
        await self._db.execute(
            "UPDATE research_missions SET context_json = ?, updated_at = ? WHERE id = ?",
            (_dump_json(merged), _now(), mission_id),
        )

    async def claim_worker_round(
        self, mission_id: str, *, token: str, now: str
    ) -> bool:
        """Atomically claim the right to run one (paid) worker round.

        Why this exists: the worker used to read ``phase`` at the start, spend
        a real LLM call, and only then transition to ``needs_input``. Two
        consumers (two runs of the same loop, two replicas sharing
        ``~/.selenespace``) could both pass the phase check before either
        finished, buying the same analysis twice while only one transition
        won. Measured on 2026-10-21: 13 missions analysed twice, 20 extra paid
        calls, and the duplicates left ``worker_started`` ×3 before a single
        ``phase_transition``.

        The guard is a single conditional UPDATE: the claim only lands when no
        unexpired claim is held and the mission is still in ``investigation``.
        ``rows`` non-empty means we own the round.

        A stale claim (worker crashed mid-round) expires on its own, so this
        cannot wedge a mission forever.
        """
        rows = await self._db.execute(
            """
            UPDATE research_missions
            SET context_json = json_set(context_json, '$.worker_claim', json(?)),
                updated_at = ?
            WHERE id = ?
              AND phase = 'investigation'
              AND (
                    json_extract(context_json, '$.worker_claim.token') IS NULL
                 OR json_extract(context_json, '$.worker_claim.until') < ?
              )
            RETURNING id
            """,
            (
                json.dumps(
                    {"token": token, "until": _claim_until(now)}, ensure_ascii=False
                ),
                now,
                mission_id,
                now,
            ),
        )
        return bool(rows)

    async def release_worker_claim(self, mission_id: str, *, token: str) -> None:
        """Drop our claim; a claim held by another token is left alone."""
        await self._db.execute(
            """
            UPDATE research_missions
            SET context_json = json_remove(context_json, '$.worker_claim')
            WHERE id = ?
              AND json_extract(context_json, '$.worker_claim.token') = ?
            """,
            (mission_id, token),
        )

    async def record_outcome(
        self, mission_id: str, outcome: dict[str, Any], *, actor: str = "selene"
    ) -> None:
        """Write the mission outcome; only valid once (resolved/parked edge).

        One-shot, two layers: an early check rejects sequential double-records,
        and the guarded UPDATE (``WHERE outcome_json = '{}'``, the schema
        default for "not yet recorded") makes a concurrent record a no-op
        instead of a silent overwrite of a settled outcome.
        """
        mission = await self.get(mission_id)
        if mission is None:
            raise KeyError(f"mission not found: {mission_id}")
        if mission["outcome"]:
            raise ValueError(
                f"outcome already recorded for {mission_id}: {mission['outcome']!r}"
            )
        await self._db.execute(
            "UPDATE research_missions SET outcome_json = ?, updated_at = ? "
            "WHERE id = ? AND outcome_json = '{}'",
            (_dump_json(outcome), _now(), mission_id),
        )
        updated = await self.get(mission_id)
        assert updated is not None
        if updated["outcome"] != outcome:
            raise ValueError(
                f"outcome already recorded for {mission_id}: {updated['outcome']!r}"
            )
        await self.append_activity(
            mission_id, actor=actor, kind="outcome_recorded", detail=outcome
        )

    # -- B2: activity stream --------------------------------------------------

    async def append_activity(
        self,
        mission_id: str,
        *,
        actor: str,
        kind: str,
        detail: dict[str, Any] | None = None,
    ) -> int:
        """Append one activity row (B2 is append-only by design)."""
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
            try:
                row_id = cursor.lastrowid or -1
            finally:
                # An open cursor keeps its statement "in progress" on the shared
                # connection even after INSERT has run; the next commit from any
                # other coroutine then fails with "SQL statements in progress".
                await cursor.close()
            await conn.execute(
                "UPDATE research_missions SET updated_at = ? WHERE id = ?",
                (_now(), mission_id),
            )
        return row_id

    async def list_activity(
        self, mission_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Oldest-first activity replay for one mission.

        注意 ``LIMIT`` 截的是**最旧**的 N 行 —— 这是"从头回放"的语义。
        要"最近的 N 行"用 ``list_recent_activity``;两者通用会静默读到
        与调用方预期相反的窗口。
        """
        rows = await self._db.execute(
            """
            SELECT * FROM mission_activity WHERE mission_id = ?
            ORDER BY occurred_at ASC, id ASC LIMIT ?
            """,
            (mission_id, limit),
        )
        result = []
        for r in rows:
            row = dict(r)
            row["detail"] = _load_json(row.pop("detail_json", None))
            result.append(row)
        return result

    async def latest_activity_of_kind(
        self, mission_id: str, kind: str
    ) -> dict[str, Any] | None:
        """The newest activity row of one kind, or ``None``.

        "最近一次 X 是什么"必须问它:扫 ``list_activity`` 的固定窗口在
        流变长后会答"没有",而界面会把这个答案原样显示成"从未发生过"。
        """
        rows = await self._db.execute(
            """
            SELECT * FROM mission_activity
            WHERE mission_id = ? AND kind = ?
            ORDER BY occurred_at DESC, id DESC LIMIT 1
            """,
            (mission_id, kind),
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["detail"] = _load_json(row.pop("detail_json", None))
        return row

    async def latest_user_activity(self, mission_id: str) -> dict[str, Any] | None:
        """The newest activity row written by a human, or ``None``."""
        rows = await self._db.execute(
            """
            SELECT * FROM mission_activity
            WHERE mission_id = ? AND actor = 'user'
            ORDER BY occurred_at DESC, id DESC LIMIT 1
            """,
            (mission_id,),
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["detail"] = _load_json(row.pop("detail_json", None))
        return row

    async def has_activity_detail(
        self, mission_id: str, *, kind: str, detail_key: str, detail_value: Any
    ) -> bool:
        """Whether this mission already has a ``kind`` row with ``detail[key] == value``.

        幂等检查必须问这个,而不是扫固定窗口的近期活动:窗口再大也会被
        足够长的活动流撑出去,写者就会重复自己。
        """
        rows = await self._db.execute(
            """
            SELECT 1 FROM mission_activity
            WHERE mission_id = ? AND kind = ?
              AND json_extract(detail_json, ?) = ?
            LIMIT 1
            """,
            (mission_id, kind, f"$.{detail_key}", detail_value),
        )
        return bool(rows)

    async def list_recent_activity(
        self, mission_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """The newest ``limit`` activity rows, still returned oldest-first.

        消费活动流末尾的调用方(worker 读用户最新指示、watcher 查幂等键)
        必须用它:``list_activity`` 在超过 ``limit`` 行后会把最新记录挡在
        窗口外,而且不会报错。
        """
        rows = await self._db.execute(
            """
            SELECT * FROM (
                SELECT * FROM mission_activity WHERE mission_id = ?
                ORDER BY occurred_at DESC, id DESC LIMIT ?
            ) ORDER BY occurred_at ASC, id ASC
            """,
            (mission_id, limit),
        )
        result = []
        for r in rows:
            row = dict(r)
            row["detail"] = _load_json(row.pop("detail_json", None))
            result.append(row)
        return result

    # -- Investigate 唯一性约束(同一对象+同一 unresolved question = 同一 Mission)

    async def list_recent_activity_all(
        self, limit: int = 50, *, include_maintenance: bool = False
    ) -> list[dict[str, Any]]:
        """跨 mission 的最近活动,降序返回,带 mission_title。

        ``list_recent_activity`` 是单 mission 内的窗口;这里回答的是
        "Selene 刚才在整个研究面上干了什么"。``mission_title`` 随行带出,
        免得调用方为每条活动再回查一次 missions 表。

        ``include_maintenance=False``(默认)跳过维护性活动:批量归档同一问句的
        重复开案会在几分钟内写出几十条同形 phase_transition,把「最近动作」整段
        刷屏成同一件事。它们仍是审计记录,只是不是研究进展。
        """
        where = ""
        if not include_maintenance:
            where = "WHERE a.detail_json NOT LIKE '%\"maintenance\": true%'"
        rows = await self._db.execute(
            f"""
            SELECT a.*, m.title AS mission_title
            FROM mission_activity a
            LEFT JOIN research_missions m ON m.id = a.mission_id
            {where}
            ORDER BY a.occurred_at DESC, a.id DESC LIMIT ?
            """,
            (max(1, limit),),
        )
        result = []
        for r in rows:
            row = dict(r)
            row["detail"] = _load_json(row.pop("detail_json", None))
            result.append(row)
        return result

    async def find_open_by_question(
        self, subject_type: str, subject_id: str, question: str
    ) -> dict[str, Any] | None:
        """Return the open (non-resolved/parked) mission for this exact question."""
        rows = await self._db.execute(
            """
            SELECT * FROM research_missions
            WHERE subject_type = ? AND subject_id = ? AND question = ?
              AND phase NOT IN ('resolved', 'parked')
            ORDER BY updated_at DESC LIMIT 1
            """,
            (subject_type, subject_id, question),
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["context"] = _load_json(row.pop("context_json", None))
        row["outcome"] = _load_json(row.pop("outcome_json", None))
        return row

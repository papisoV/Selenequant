"""Question 仓储 —— 研究组织的核心锚点。

Question 不是"所有对象的父节点",是研究的**锚**:它决定哪些 Episode /
Evidence / Memory 有资格进入这个问题的研究世界(§2 Anchor = scope)。

两轴纪律落在这里:
- 状态轴可逆 → status 可 open→终态→open,但每次变化都追加一行事件,
  历史不会被 UPDATE 覆盖
- 回答轴只增 → Revision 只 INSERT,seq 单调

add_revision 的引用验证是本文件存在的核心理由:它把"第二次回答必须真的
利用第一次研究留下的东西"从口号变成一条会抛错的断言。软性的"我们记得引用"
靠不住 —— 三个月后没人会去看引用集有没有退化。
"""

from __future__ import annotations

from typing import Any

from selene_space_storage.repositories.research_graph._shared import (
    _dump_json,
    _load_json,
    _now,
    new_id,
)

STATUS_OPEN = "open"
TERMINAL_STATUSES = frozenset(
    {"sufficiently_answered", "falsified", "parked", "superseded"}
)
ALL_STATUSES = frozenset({STATUS_OPEN}) | TERMINAL_STATUSES


def is_proposed_question(row: dict[str, Any]) -> bool:
    """系统提议问题的唯一可区分组合(受保护不变量)。

    created_by='selene' 且 parent_question_id 非空 = 调查会主持人提出的
    待拍板新问题。fork/succeed/_spawn 的 actor 校验保证用户动作产生的
    子问题永远不会撞进这个组合 —— 靠约定不靠看守的组合必然腐烂。
    """
    return (
        row.get("created_by") == "selene" and row.get("parent_question_id") is not None
    )


def _reject_selene_actor(actor: str) -> None:
    """系统提议只经 create(created_by='selene') 一条路进入;fork/succeed
    传 actor='selene' 会被 _spawn 透传进 created_by,撞坏上面那个组合 ——
    fail fast。"""
    if actor == "selene":
        raise ValueError(
            "actor='selene' is reserved for system-proposed questions; "
            "use create() with parent_question_id instead"
        )


# 九张表里凡是可以被引用的,按 id 前缀反查表名。
# ``edge-`` 是第十个可引用 id 空间(边指向边,D-5 撤销模型),但它在
# ``integrity._node_exists`` 里特判 —— 不在这张表里。
_ID_PREFIX_TABLES: tuple[tuple[str, str], ...] = (
    ("q-", "research_questions"),
    ("rev-", "research_revisions"),
    ("ep-", "research_episodes"),
    ("ev-", "research_evidence"),
    ("hyp-", "research_hypotheses"),
    ("exp-", "research_experiments"),
    ("dec-", "research_decisions"),
    # ``mis-`` 必须在这里:mission 是一等边端点(promote 管线写
    # ``("mission", mid) → ("question", qid)`` 的 addresses 边)。漏了它,
    # ``_node_exists`` 反查不到表 → 每条 mission 端点的边都被规则 3 误报成
    # ``dangling_edge``(2026-10-13 实测 ``edge-8a7a7e42a0a3`` from=
    # ``mis-b2f9056975c5``:mission 明明存在却报"from endpoint not found")。
    ("mis-", "research_missions"),
)


class ResearchQuestionRepository:
    """Facade over ``research_questions`` + ``research_question_events`` + ``research_revisions``."""

    def __init__(self, db: Any) -> None:
        self._db = db

    # -- 身份 ---------------------------------------------------------------

    async def create(
        self,
        title: str,
        body: str = "",
        anchor: dict[str, Any] | None = None,
        created_by: str = "director",
        parent_question_id: str | None = None,
    ) -> str:
        """立一个新问题。CREATE 只生 Question,不自动开 Mission(§6 两闸分开)。"""
        question_id = new_id("q")
        now = _now()
        await self._db.execute(
            """
            INSERT INTO research_questions (
                id, title, body, anchor_json, status,
                parent_question_id, opened_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question_id,
                title,
                body,
                _dump_json(anchor or {}),
                STATUS_OPEN,
                parent_question_id,
                now,
                now,
                created_by,
            ),
        )
        await self._append_event(
            question_id,
            actor="system",
            kind="created",
            detail={"title": title, "parent_question_id": parent_question_id},
        )
        return question_id

    async def get(self, question_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM research_questions WHERE id = ?", (question_id,)
        )
        if not rows:
            return None
        row = self._hydrate(dict(rows[0]))
        revisions = await self.list_revisions(question_id)
        row["revision_count"] = len(revisions)
        row["current_revision"] = revisions[-1] if revisions else None
        return row

    async def list_questions(
        self,
        status: str | None = None,
        limit: int = 50,
        parent: str | None = None,
        created_by: str | None = None,
    ) -> list[dict[str, Any]]:
        if status is not None and status not in ALL_STATUSES:
            raise ValueError(f"unknown status: {status!r}")
        where = ["1=1"]
        params: list[Any] = []
        if status is not None:
            where.append("status = ?")
            params.append(status)
        if parent is not None:
            where.append("parent_question_id = ?")
            params.append(parent)
        if created_by is not None:
            where.append("created_by = ?")
            params.append(created_by)
        rows = await self._db.execute(
            "SELECT * FROM research_questions WHERE "
            + " AND ".join(where)
            + " ORDER BY updated_at DESC LIMIT ?",
            (*params, limit),
        )
        out = [self._hydrate(dict(r)) for r in rows]
        # 列表页要显示每问的回答版数;一次聚合比 N+1 次 list_revisions 便宜
        counts = await self._db.execute(
            "SELECT question_id, COUNT(*) AS n FROM research_revisions "
            "GROUP BY question_id"
        )
        by_qid = {r["question_id"]: r["n"] for r in counts}
        for q in out:
            q["revision_count"] = by_qid.get(q["id"], 0)
        return out

    # -- 状态轴(可逆) -------------------------------------------------------

    async def close(
        self,
        question_id: str,
        terminal_status: str,
        *,
        actor: str = "user",
        note: str | None = None,
    ) -> dict[str, Any]:
        """进四终态之一。Question 的"结束"是状态,不是对象生命周期终结(C-2)。"""
        if terminal_status not in TERMINAL_STATUSES:
            raise ValueError(f"not a terminal status: {terminal_status!r}")
        question = await self.get(question_id)
        if question is None:
            raise KeyError(f"question not found: {question_id}")
        now = _now()
        # Guarded update: WHERE carries the status we read so a concurrent
        # close/reopen is a no-op — the event row must never record a "from"
        # value that was already stale when written.
        await self._db.execute(
            "UPDATE research_questions SET status = ?, updated_at = ?, closed_at = ? "
            "WHERE id = ? AND status = ?",
            (terminal_status, now, now, question_id, question["status"]),
        )
        updated = await self.get(question_id)
        assert updated is not None
        if updated["status"] != terminal_status:
            raise ValueError(
                f"question status changed concurrently for {question_id}: "
                f"expected from {question['status']!r}, actual {updated['status']!r}"
            )
        await self._append_event(
            question_id,
            actor=actor,
            kind="closed",
            detail={
                "from": question["status"],
                "to": terminal_status,
                **({"note": note} if note else {}),
            },
        )
        return updated

    async def reopen(
        self, question_id: str, *, actor: str = "user", reason: str
    ) -> dict[str, Any]:
        """终态 → open。状态可以回退,回答和关系永不回退(§2 三轴)。"""
        question = await self.get(question_id)
        if question is None:
            raise KeyError(f"question not found: {question_id}")
        if question["status"] not in TERMINAL_STATUSES:
            raise ValueError(
                f"question is not closed, nothing to reopen: {question['status']!r}"
            )
        now = _now()
        # Guarded update: same rationale as close().
        await self._db.execute(
            "UPDATE research_questions SET status = ?, updated_at = ?, closed_at = NULL "
            "WHERE id = ? AND status = ?",
            (STATUS_OPEN, now, question_id, question["status"]),
        )
        updated = await self.get(question_id)
        assert updated is not None
        if updated["status"] != STATUS_OPEN:
            raise ValueError(
                f"question status changed concurrently for {question_id}: "
                f"expected from {question['status']!r}, actual {updated['status']!r}"
            )
        await self._append_event(
            question_id,
            actor=actor,
            kind="reopened",
            detail={"from": question["status"], "reason": reason},
        )
        return updated

    # -- 五事件:分叉 vs successor 只差一行 ----------------------------------

    async def fork(
        self, question_id: str, title: str, body: str = "", *, actor: str = "user"
    ) -> str:
        """分叉:新问题出生,**原问题继续活着**(C-3 身份独立判定)。"""
        _reject_selene_actor(actor)
        return await self._spawn(
            question_id, title, body, actor=actor, end_original=False
        )

    async def succeed(
        self, question_id: str, title: str, body: str = "", *, actor: str = "user"
    ) -> str:
        """取代:新问题出生,**原问题结束**。分叉与 successor 只差这一行。"""
        _reject_selene_actor(actor)
        return await self._spawn(
            question_id, title, body, actor=actor, end_original=True
        )

    async def _spawn(
        self,
        question_id: str,
        title: str,
        body: str,
        *,
        actor: str,
        end_original: bool,
    ) -> str:
        _reject_selene_actor(actor)
        parent = await self.get(question_id)
        if parent is None:
            raise KeyError(f"question not found: {question_id}")
        child_id = await self.create(
            title=title,
            body=body,
            anchor=parent["anchor"],
            created_by=actor,
            parent_question_id=question_id,
        )
        if end_original:
            now = _now()
            await self._db.execute(
                "UPDATE research_questions SET status = 'superseded', "
                "updated_at = ?, closed_at = ? WHERE id = ?",
                (now, now, question_id),
            )
            await self._append_event(
                question_id,
                actor=actor,
                kind="superseded",
                detail={"successor_id": child_id},
            )
        await self._append_event(
            child_id,
            actor=actor,
            kind="forked" if not end_original else "superseding",
            detail={"parent_question_id": question_id},
        )
        return child_id

    # -- 回答轴(只增) -------------------------------------------------------

    async def add_revision(
        self,
        question_id: str,
        content: str,
        *,
        actor: str = "selene",
        rationale: list[str] | None = None,
        cited: tuple[str, ...] | list[str] = (),
        horizon: str = "structural",
        structured: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """追加一版回答。

        ``cited`` 里的每个 id 都必须**真实存在**且**与本问题有边相连**
        (本问题自己的早期 revision 除外)。这条验证是 §11 验收判据的载体:
        如果允许悬空引用,"第二次回答利用了第一次的成果"就无法被检查,
        只能靠自觉 —— 而自觉在三个月后必然退化。
        """
        question = await self.get(question_id)
        if question is None:
            raise KeyError(f"question not found: {question_id}")
        cited_list = list(dict.fromkeys(cited))  # 去重保序
        await self._validate_citations(question_id, cited_list)
        next_seq = question["revision_count"] + 1
        revision_id = new_id("rev")
        await self._db.execute(
            """
            INSERT INTO research_revisions (
                id, question_id, seq, content, rationale_json, cited_json, actor, horizon, created_at,
                structured_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                question_id,
                next_seq,
                content,
                _dump_json(rationale or []),
                _dump_json(cited_list),
                actor,
                horizon,
                _now(),
                _dump_json(structured or {}),
            ),
        )
        await self._db.execute(
            "UPDATE research_questions SET updated_at = ? WHERE id = ?",
            (_now(), question_id),
        )
        row = await self._get_revision(revision_id)
        assert row is not None
        return row

    async def list_revisions(self, question_id: str) -> list[dict[str, Any]]:
        rows = await self._db.execute(
            "SELECT * FROM research_revisions WHERE question_id = ? ORDER BY seq ASC",
            (question_id,),
        )
        return [self._hydrate_revision(dict(r)) for r in rows]

    async def citation_migration(self, question_id: str) -> dict[str, Any]:
        """相邻两版回答的引用集差异 —— §4「引用迁移」的可观测形式。

        注意:这只是**证据**,不是"更准"的定义。断言有没有变好,由下游的
        Decision / Outcome 决定,不在这里打分。
        """
        revisions = await self.list_revisions(question_id)
        if len(revisions) < 2:
            latest = revisions[0] if revisions else None
            return {
                "question_id": question_id,
                "from_revision_id": None,
                "to_revision_id": latest["id"] if latest else None,
                "added": [],
                "dropped": [],
                "retained": [],
            }
        prev, curr = revisions[-2], revisions[-1]
        prev_set, curr_set = set(prev["cited"]), set(curr["cited"])
        return {
            "question_id": question_id,
            "from_revision_id": prev["id"],
            "to_revision_id": curr["id"],
            "added": sorted(curr_set - prev_set),
            "dropped": sorted(prev_set - curr_set),
            "retained": sorted(curr_set & prev_set),
        }

    # -- 事件流 -------------------------------------------------------------

    async def list_events(
        self, question_id: str, limit: int = 200, kind: str | None = None
    ) -> list[dict[str, Any]]:
        """按 occurred_at 升序回放事件;kind 过滤供消费方只取一类
        (如 P1 消息流按 investigate_perspective 读,绕开 200 条窗口截断)。"""
        if kind is None:
            rows = await self._db.execute(
                "SELECT * FROM research_question_events WHERE question_id = ? "
                "ORDER BY occurred_at ASC, id ASC LIMIT ?",
                (question_id, limit),
            )
        else:
            rows = await self._db.execute(
                "SELECT * FROM research_question_events WHERE question_id = ? "
                "AND kind = ? ORDER BY occurred_at ASC, id ASC LIMIT ?",
                (question_id, kind, limit),
            )
        result = []
        for r in rows:
            row = dict(r)
            row["detail"] = _load_json(row.pop("detail_json", None))
            result.append(row)
        return result

    async def list_recent_events(
        self, *, kinds: tuple[str, ...], limit: int = 50
    ) -> list[dict[str, Any]]:
        """跨问题取最近事件(降序),带 question_title —— 供 agent-activity 聚合。

        与 ``list_events`` 的单问题升序窗口相反:这里是"Selene 最近在做什么"
        的全局流,按时间**最近**截断,而不是从最早一条数起。``question_title``
        随行带出,免得调用方为每条事件再回查一次问题表。
        """
        if not kinds:
            return []
        placeholders = ",".join("?" for _ in kinds)
        rows = await self._db.execute(
            "SELECT e.*, q.title AS question_title FROM research_question_events e "
            "LEFT JOIN research_questions q ON q.id = e.question_id "
            f"WHERE e.kind IN ({placeholders}) "
            "ORDER BY e.occurred_at DESC, e.id DESC LIMIT ?",
            (*kinds, max(1, limit)),
        )
        result = []
        for r in rows:
            row = dict(r)
            row["detail"] = _load_json(row.pop("detail_json", None))
            result.append(row)
        return result

    # -- 内部 ---------------------------------------------------------------

    async def _validate_citations(self, question_id: str, cited: list[str]) -> None:
        if not cited:
            return
        own_revisions = {r["id"] for r in await self.list_revisions(question_id)}
        unknown: list[str] = []
        unconnected: list[str] = []
        for node_id in cited:
            if node_id in own_revisions:
                continue  # 引用本问题自己的早期 revision,永远合法
            if not await self._node_exists(node_id):
                unknown.append(node_id)
                continue
            if not await self._connected_to_question(question_id, node_id):
                unconnected.append(node_id)
        if unknown:
            raise ValueError(f"unknown cited object(s): {', '.join(unknown)}")
        if unconnected:
            raise ValueError(
                "cited object(s) not connected to this question: "
                + ", ".join(unconnected)
            )

    async def _node_exists(self, node_id: str) -> bool:
        for prefix, table in _ID_PREFIX_TABLES:
            if node_id.startswith(prefix):
                rows = await self._db.execute(
                    f"SELECT 1 FROM {table} WHERE id = ? LIMIT 1", (node_id,)
                )
                return bool(rows)
        return False

    async def _connected_to_question(self, question_id: str, node_id: str) -> bool:
        """任一方向的边都算连通 —— 边是"这个对象进入了这个问题的研究世界"的凭证。"""
        rows = await self._db.execute(
            """
            SELECT 1 FROM research_edges
            WHERE (from_type = 'question' AND from_id = ? AND to_id = ?)
               OR (to_type = 'question' AND to_id = ? AND from_id = ?)
               OR (from_id = ? AND to_id = ?)
               OR (from_id = ? AND to_id = ?)
            LIMIT 1
            """,
            (
                question_id,
                node_id,
                question_id,
                node_id,
                question_id,
                node_id,
                node_id,
                question_id,
            ),
        )
        return bool(rows)

    async def _get_revision(self, revision_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM research_revisions WHERE id = ?", (revision_id,)
        )
        if not rows:
            return None
        return self._hydrate_revision(dict(rows[0]))

    async def append_event(
        self,
        question_id: str,
        *,
        actor: str,
        kind: str,
        detail: dict[str, Any] | None = None,
    ) -> int:
        """公开入口:追加事件并返回新事件 id(events.id 是自增 INTEGER)。"""
        return await self._append_event(
            question_id, actor=actor, kind=kind, detail=detail
        )

    async def _append_event(
        self,
        question_id: str,
        *,
        actor: str,
        kind: str,
        detail: dict[str, Any] | None = None,
    ) -> int:
        if actor not in ("selene", "user", "system"):
            raise ValueError(f"unknown actor: {actor!r}")
        # db.execute 门面会关 cursor 取不到 lastrowid,必须走 connection() 作用域
        # (先例:research_missions.append_activity)
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "INSERT INTO research_question_events "
                "(question_id, occurred_at, actor, kind, detail_json) VALUES (?, ?, ?, ?, ?)",
                (question_id, _now(), actor, kind, _dump_json(detail or {})),
            )
            row_id = cursor.lastrowid or -1
        return row_id

    @staticmethod
    def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
        row["anchor"] = _load_json(row.pop("anchor_json", None)) or {}
        return row

    @staticmethod
    def _hydrate_revision(row: dict[str, Any]) -> dict[str, Any]:
        row["rationale"] = _load_json(row.pop("rationale_json", None)) or []
        row["cited"] = _load_json(row.pop("cited_json", None)) or []
        # v030 之前落库的行没有这一列 → 给 {}，别让调用方区分 "没写" 与
        # "列不存在"：两者对阅读者都是"这一版没有可判定字段"。
        row["structured"] = _load_json(row.pop("structured_json", None)) or {}
        return row

    # ---- v025 主动复访 -------------------------------------------------------
    # "工具保存答案,更聪明的工具保存问题与历史"的下一步:历史主动找上门来。
    # open/parked 才可设 —— 已终态的问题(sufficiently_answered/falsified/
    # superseded)不需要被提醒复访;closed_at 非空同理。

    _REVISIT_ALLOWED_STATUSES = ("open", "parked")

    async def set_revisit(
        self,
        question_id: str,
        next_revisit_at: str | None,
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        """设/清复访时点。``None`` = 清除计划(事件 kind=revisit_cleared)。"""
        if actor not in ("selene", "user", "system"):
            raise ValueError(f"unknown actor: {actor!r}")
        question = await self.get(question_id)
        if question is None:
            raise KeyError(f"question not found: {question_id}")
        if question["status"] not in self._REVISIT_ALLOWED_STATUSES:
            raise ValueError(
                f"cannot set revisit on a {question['status']} question; "
                "only open/parked questions accept a revisit plan"
            )
        await self._db.execute(
            "UPDATE research_questions SET next_revisit_at = ?, updated_at = ? WHERE id = ?",
            (next_revisit_at, _now(), question_id),
        )
        kind = "revisit_scheduled" if next_revisit_at is not None else "revisit_cleared"
        await self._append_event(
            question_id,
            actor=actor,
            kind=kind,
            detail={"next_revisit_at": next_revisit_at},
        )
        return await self.get(question_id)  # type: ignore[return-value]

    async def due(
        self,
        now: str,
        *,
        limit: int = 20,
        after: tuple[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """到期该复访的问题清单。

        ``now`` 必填无默认 —— 时间判定必须显式注入(测试确定性 + 调用方
        自己负责"现在"的语义)。只看 open/parked;终态不召回。

        ``after`` 是 keyset 游标 ``(next_revisit_at, id)``:传上一页最后一行的
        这两列取下一页。排序是 ``(next_revisit_at, id)`` 全序,所以游标不会漏行
        也不会重复。需要它,是因为"到期"状态不会因消费而消失(复访提醒不改
        问题本身):只按 ``limit`` 取头的调用方,在积压超过一页后永远看不到
        后面的行。
        """
        where = [
            "next_revisit_at IS NOT NULL",
            "next_revisit_at <= ?",
            "status IN ('open','parked')",
        ]
        params: list[Any] = [now]
        if after is not None:
            where.append("(next_revisit_at, id) > (?, ?)")
            params.extend([after[0], after[1]])
        rows = await self._db.execute(
            "SELECT id, title, anchor_json, status, next_revisit_at, opened_at, updated_at "
            "FROM research_questions WHERE "
            + " AND ".join(where)
            + " ORDER BY next_revisit_at ASC, id ASC LIMIT ?",
            (*params, limit),
        )
        return [self._hydrate(dict(r)) for r in rows]

    async def count_by_status(self) -> dict[str, int]:
        """每种状态的问题**总数**(不受任何 ``limit`` 影响)。

        TODAY 顶条七环里"问句"曾经直接复用 mission 计数,于是"问句"和"使命"
        两环永远显示同一个数字——两环的数据源不同,数字相同只会让人以为
        计数坏了。计数取全量,不取列表截断后的长度。
        """
        rows = await self._db.execute(
            "SELECT status, COUNT(*) AS n FROM research_questions GROUP BY status"
        )
        return {str(r["status"]): int(r["n"]) for r in rows or []}

    async def count_due(self, now: str) -> int:
        """到期问题的**总数**(不受 ``due()`` 的 ``limit`` 影响)。

        列表可以截断,计数不能 —— "N 个问题到了复访时点"若拿截断后的
        ``len(rows)`` 去说,积压一多就在撒谎。
        """
        rows = await self._db.execute(
            "SELECT COUNT(*) AS n FROM research_questions "
            "WHERE next_revisit_at IS NOT NULL AND next_revisit_at <= ? "
            "AND status IN ('open','parked')",
            (now,),
        )
        return int(rows[0]["n"]) if rows else 0

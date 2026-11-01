"""§9.5 Research Graph Integrity Check —— 把纪律变成能自动验的东西。

D-3 / D-4 / D-5 / C-4 原本都是"纪律":写在文档里,靠人记得遵守。这份审计
把它们逐条变成可以跑的查询 —— 因为纪律在三个月后必然退化,而测试不会。

审计是**只读**的:它报告违规,不自动修复。自动修复会撞 C-4(不许为了让
当前状态看起来连贯而回头改写历史)。发现问题 → 人来决定怎么追加一条
纠正记录,而不是让审计器悄悄擦掉证据。
"""

from __future__ import annotations

from typing import Any

from selene_space_storage.repositories.research_graph._shared import _load_json
from selene_space_storage.repositories.research_graph.questions import (
    _ID_PREFIX_TABLES,
)
from selene_space_storage.repositories.research_graph.recall import (
    _evidence_chain,
    _kind_of,
)

# 每个"可以被边的端点指向"的领域对象:前缀 → (表, 出生时间列)。
# 规则 7(历史边是否被改过)靠它。列名必须逐字对上 DDL —— 写错的行只在
# 真有边指向那个前缀时才炸(见 test_dated_table_columns_really_exist)。
_DATED_TABLES: tuple[tuple[str, str, str], ...] = (
    ("q-", "research_questions", "opened_at"),
    ("rev-", "research_revisions", "created_at"),
    ("ep-", "research_episodes", "created_at"),
    ("ev-", "research_evidence", "created_at"),
    ("hyp-", "research_hypotheses", "created_at"),
    ("exp-", "research_experiments", "created_at"),
    ("dec-", "research_decisions", "decided_at"),
)


def _table_for(node_id: str) -> str | None:
    for prefix, table in _ID_PREFIX_TABLES:
        if node_id.startswith(prefix):
            return table
    return None


async def _node_exists(db: Any, node_id: str) -> bool:
    # **边也是节点。** revokes 边的两端指向的是边本身(D-5 的撤销模型),
    # 而边 id 不在 _ID_PREFIX_TABLES 里(那张表只管领域对象)。
    # 不特判这一支,每条 revokes 边都会被规则 3 误报成 dangling_edge。
    if node_id.startswith("edge-"):
        rows = await db.execute(
            "SELECT 1 FROM research_edges WHERE id = ? LIMIT 1", (node_id,)
        )
        return bool(rows)
    table = _table_for(node_id)
    if table is None:
        return False
    rows = await db.execute(f"SELECT 1 FROM {table} WHERE id = ? LIMIT 1", (node_id,))
    return bool(rows)


async def check_graph_integrity(db: Any) -> dict[str, Any]:
    """跑 §9.5 的九条规则,返回违规清单、扩锚信号与对象计数。

    ``violations`` 与 ``scope_pressure`` **必须分开看**:前者是错误
    (``ok=False``),后者按 D-6 是**正常状态** —— 它该触发 PROPOSE EXPAND,
    不该被当作错误自动纠正。
    """
    violations: list[dict[str, Any]] = []
    scope_pressure: list[dict[str, Any]] = []

    edges = await db.execute("SELECT * FROM research_edges")
    for row in edges:
        edge = dict(row)

        # 规则 1:D-3 —— 判断边必须带推理依据与出处
        if edge["edge_class"] == "judgment":
            has_rationale = bool((edge["rationale"] or "").strip())
            has_provenance = edge["provenance_json"] not in ("[]", "", "null")
            if not (has_rationale and has_provenance):
                violations.append(
                    {
                        "rule": "judgment_without_rationale",
                        "object_id": edge["id"],
                        "detail": "judgment edge missing rationale or provenance",
                    }
                )

        # 规则 2:D-3 —— 事实边不得夹带认识论判断
        if edge["edge_class"] == "fact" and edge["kind"] != "revokes":
            if (edge["rationale"] or "").strip() or edge["provenance_json"] not in (
                "[]",
                "",
                "null",
            ):
                violations.append(
                    {
                        "rule": "inference_recorded_as_fact",
                        "object_id": edge["id"],
                        "detail": "fact edge carries rationale/provenance — inference dressed as fact",
                    }
                )

        # 规则 3:引用完整性 —— 两端必须真实存在
        for side in ("from", "to"):
            node_id = edge[f"{side}_id"]
            if not await _node_exists(db, node_id):
                violations.append(
                    {
                        "rule": "dangling_edge",
                        "object_id": edge["id"],
                        "detail": f"{side} endpoint not found: {node_id}",
                    }
                )

        # 规则 4:D-5 —— 撤销最多一层
        if edge["kind"] == "revokes":
            target_rows = await db.execute(
                "SELECT kind FROM research_edges WHERE id = ? LIMIT 1",
                (edge["to_id"],),
            )
            if target_rows and dict(target_rows[0])["kind"] == "revokes":
                violations.append(
                    {
                        "rule": "recursive_revocation",
                        "object_id": edge["id"],
                        "detail": f"revokes edge targets another revokes edge: {edge['to_id']}",
                    }
                )

    # 规则 5:Evidence 必须挂在真实 Episode 上
    evidence_rows = await db.execute(
        "SELECT e.id, e.episode_id FROM research_evidence e "
        "LEFT JOIN research_episodes ep ON ep.id = e.episode_id "
        "WHERE ep.id IS NULL"
    )
    for row in evidence_rows:
        item = dict(row)
        violations.append(
            {
                "rule": "evidence_without_episode",
                "object_id": item["id"],
                "detail": f"episode not found: {item['episode_id']}",
            }
        )

    # 规则 6:§8④ —— outcome_never 必须有观察窗口
    never_rows = await db.execute(
        "SELECT id FROM research_decisions "
        "WHERE outcome_status = 'outcome_never' AND (window_end IS NULL OR window_end = '')"
    )
    for row in never_rows:
        item = dict(row)
        violations.append(
            {
                "rule": "outcome_never_without_window",
                "object_id": item["id"],
                "detail": "outcome_never without an observation window — 'not yet' recorded as 'never'",
            }
        )

    # 规则 7:§9.5「是否修改了历史边?」
    # 表全是 append-only,所以判据是"**插入造不出来的顺序关系**":
    # 一条边指向一个比它自己更晚出生的对象 —— 只有回头改写历史才做得到。
    #
    # ponytail: 每条边测 14 次存在性查询。这是只读审计、手动/CI 触发,
    # 不是热路径;等到审计变慢再合并成一次 JOIN。
    for row in edges:
        edge = dict(row)
        for prefix, table, column in _DATED_TABLES:
            for side in ("from", "to"):
                node_id = edge[f"{side}_id"]
                if not node_id.startswith(prefix):
                    continue
                born_rows = await db.execute(
                    f"SELECT {column} AS born_at FROM {table} WHERE id = ? LIMIT 1",
                    (node_id,),
                )
                if not born_rows:
                    continue
                born_at = dict(born_rows[0])["born_at"]
                if born_at and born_at > edge["created_at"]:
                    violations.append(
                        {
                            "rule": "history_edge_rewritten",
                            "object_id": edge["id"],
                            "detail": (
                                f"edge created at {edge['created_at']} but its "
                                f"{side} endpoint {node_id} was born at {born_at}"
                            ),
                        }
                    )

    # 规则 8:§7.4 / §6.10 —— **是否存在无证据 Memory**
    # 可执行形态:**被别的回答引用过、但自己的证据链走不到 Episode / Evidence
    # 的结论**。它已经被当作既有知识消费了,而它没有这个资格。
    # 这是 §7.4 从"纪律"变成"能自动验的东西"的地方 —— 直接复用 recall 的走边逻辑。
    for row in await db.execute("SELECT id, cited_json FROM research_revisions"):
        revision = dict(row)
        for cited_id in _load_json(revision["cited_json"]) or []:
            cited_kind = _kind_of(cited_id)
            if cited_kind not in ("revision", "hypothesis"):
                continue
            if not await _evidence_chain(db, cited_kind, cited_id):
                violations.append(
                    {
                        "rule": "memory_without_evidence_chain",
                        "object_id": cited_id,
                        "detail": (
                            f"cited by {revision['id']} but its evidence chain "
                            "reaches neither Episode nor Evidence"
                        ),
                    }
                )

    # 规则 9:D-6 —— 跨 Anchor scope 自动吸收
    # ⚠️ **这不是违规。** D-6 写死了「锚外引边 = scope pressure = 扩锚信号,
    # **不是错误**」→ 系统 PROPOSE EXPAND,Director 拍。
    # 所以它进单独的 scope_pressure 表,**不进 violations**;把它算成错误
    # 就等于让图自己去矛盾,那是 D-4 禁止的。
    for row in await db.execute(
        "SELECT e.id AS edge_id, ev.id AS evidence_id, e.to_id AS question_id, "
        "       ep.subject_id AS subject_id, q.anchor_json AS anchor_json "
        "FROM research_edges e "
        "JOIN research_evidence ev ON ev.id = e.from_id "
        "JOIN research_episodes ep ON ep.id = ev.episode_id "
        "JOIN research_questions q ON q.id = e.to_id "
        "WHERE e.from_type = 'evidence' AND e.to_type = 'question'"
    ):
        item = dict(row)
        anchor = _load_json(item["anchor_json"]) or {}
        strategy_ids = set(anchor.get("strategy_ids") or ())
        if strategy_ids and item["subject_id"] not in strategy_ids:
            scope_pressure.append(
                {
                    "kind": "anchor_expand_candidate",
                    "object_id": item["evidence_id"],
                    "question_id": item["question_id"],
                    "detail": (
                        f"subject {item['subject_id']} is outside the anchor of "
                        f"{item['question_id']} — propose EXPAND, do not auto-absorb"
                    ),
                }
            )

    # 规则 10:关系可达性 —— revision/evidence 必须能被"列或边"读到其宿主。
    #
    # **不同于"必须有边"**。两列(NOT NULL 外键)本身就是关系的载体,边表只
    # 承载列表达不了的关系(见 _shared.py 注册表约定)。所以正常数据天然通路,
    # 这条规则抓的是**列被清空 / 有人绕过仓储写行**的异常 —— 那种行会在图里
    # 变成孤立节点,而任何单一读法都发现不了。
    #
    # 判据用 SQL 而非 list_relations:审计只关心"读得到吗",不必把 30 条
    # 派生行全构造出来。
    unreadable_revisions = await db.execute(
        "SELECT r.id FROM research_revisions r "
        "WHERE (r.question_id IS NULL OR r.question_id = '') "
        "AND NOT EXISTS (SELECT 1 FROM research_edges e "
        "  WHERE e.from_type = 'revision' AND e.from_id = r.id "
        "  AND e.kind = 'revision_of')"
    )
    for row in unreadable_revisions:
        violations.append(
            {
                "rule": "revision_relation_unreadable",
                "object_id": dict(row)["id"],
                "detail": (
                    "revision has neither a question_id column nor a "
                    "revision_of edge — it is unreachable from any question"
                ),
            }
        )

    unreadable_evidence = await db.execute(
        "SELECT v.id FROM research_evidence v "
        "WHERE (v.episode_id IS NULL OR v.episode_id = '') "
        "AND NOT EXISTS (SELECT 1 FROM research_edges e "
        "  WHERE e.from_type = 'evidence' AND e.from_id = v.id "
        "  AND e.kind = 'observed_from')"
    )
    for row in unreadable_evidence:
        violations.append(
            {
                "rule": "evidence_relation_unreadable",
                "object_id": dict(row)["id"],
                "detail": (
                    "evidence has neither an episode_id column nor an "
                    "observed_from edge — it is unreachable from any episode"
                ),
            }
        )

    # 规则 11:命名主体完整性 —— mission 的 subject_id 必须真的存在
    #
    # ``strategy`` 主体有登记表(``strategies``),可硬校验;``subject_id`` 指向
    # 不存在的策略 = 驾驶舱 State 端点必然 404 的幽灵 Mission。此前没有任何
    # 写入面校验存在性,于是探针名/回测 id 静默进了 strategy 主体(2026-10-13
    # 实测 ``mis-b2f9056975c5`` subject=``entry-delay-ic-probe``、
    # ``mis-3adeb596e736`` subject=``bt-b62d32ac``)。L1 的写入门挡住新脏数据,
    # 本规则负责把**既有**幽灵 Mission 检出来 —— 审计只报告,不改历史(C-4)。
    #
    # ``market``/``event``/``system`` 无登记表(或语义自由),不在本规则范围;
    # 它们的存在性判据是后续扩展,不是遗漏。
    strategy_missions = await db.execute(
        "SELECT m.id, m.subject_id FROM research_missions m "
        "LEFT JOIN strategies s ON s.id = m.subject_id "
        "WHERE m.subject_type = 'strategy' AND s.id IS NULL"
    )
    for row in strategy_missions:
        item = dict(row)
        violations.append(
            {
                "rule": "mission_subject_dangling",
                "object_id": item["id"],
                "detail": (
                    f"strategy mission subject_id {item['subject_id']!r} is not "
                    "a registered strategy — State endpoint will 404"
                ),
            }
        )

    counts = {
        "questions": len(await db.execute("SELECT 1 FROM research_questions")),
        "episodes": len(await db.execute("SELECT 1 FROM research_episodes")),
        "evidence": len(await db.execute("SELECT 1 FROM research_evidence")),
        "edges": len(edges),
        "decisions": len(await db.execute("SELECT 1 FROM research_decisions")),
    }
    return {
        "ok": not violations,
        "violations": violations,
        "scope_pressure": scope_pressure,
        "counts": counts,
    }

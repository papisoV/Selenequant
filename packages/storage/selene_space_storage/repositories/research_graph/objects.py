"""六个独立一等研究对象 —— Episode / Evidence / Hypothesis / Experiment / Decision。

它们**不属于**任何一个 Question 或 Mission,通过关系边连接(§6)。同一条
Hypothesis 可以同时服务 Q1/Q2/Q3;同一条 Evidence 可以同时 supports Q1 的
revision、supports H2、contradicts Q3。

本文件的三条硬约束:
- EpisodeRepository 不提供任何 update 方法(§8③)。事件永远没变,观察在变
- DecisionRepository.record() 不接受 decided_by —— 恒为 director(§6)
- outcome_never 必须配一个已经走完的观察窗口(§8④),否则"没等够"会被
  记成"永远不会发生",§4 的"正确的不行动"就守不住了
"""

from __future__ import annotations

import hashlib
from typing import Any

from selene_space_storage.repositories.research_graph._shared import (
    _dump_json,
    _is_past,
    _load_json,
    _now,
    new_id,
)
from selene_space_storage.repositories.research_graph.error_replay import ERROR_LAYERS

EVIDENCE_MODES = ("RECORDED", "RECONSTRUCTED", "AFTER_THE_FACT")

HYPOTHESIS_STATUSES = ("proposed", "testing", "supported", "refuted", "retired")

# 生命周期状态轴(吸收 external-research-project 词汇表)。这是 **per-episode 观察周期
# 状态**,不是因子级状态:快照契约"新快照=新 episode"意味着 LIVE 因子的
# 下一次重快照会带初始 DISCOVERED 的新 episode;因子级状态由消费方从
# derived_from 链推导,storage 不做结转(RETIRED 结转 = 复活)。
LIFECYCLE_STATUSES = (
    "DISCOVERED",
    "INSPECTED",
    "PAPER_TRADING",
    "LIVE",
    "RETIRED",
)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "DISCOVERED": frozenset({"INSPECTED", "RETIRED"}),
    "INSPECTED": frozenset({"PAPER_TRADING", "RETIRED"}),
    "PAPER_TRADING": frozenset({"LIVE", "RETIRED"}),
    "LIVE": frozenset({"RETIRED"}),
    "RETIRED": frozenset(),  # 终态,无出边
}

OUTCOME_PENDING = "outcome_pending"
OUTCOME_OBSERVED = "outcome_observed"
OUTCOME_NEVER = "outcome_never"


# ---------------------------------------------------------------------------
# Episode —— 不可改
# ---------------------------------------------------------------------------


async def link_derived_from(
    db: Any,
    *,
    child_episode_id: str,
    subject_type: str,
    subject_id: str,
) -> str | None:
    """同 subject 前一 episode → 新 episode 连 derived_from 事实边(子→父)。

    storage 层共用 helper:快照(factor_snapshot.save_snapshot)与探针
    (run_probe.write_probe_results)两写入方 import 同一份逻辑 —— 不复制
    (check_question_anchor 双份逐字漂移是现成病历)。

    - 排序 ``rowid DESC`` 且 **rowid 严格小于子集**:父必须是插入序上真正
      早于子的那一集。只查"最新他人"会在竞态(后建的集先连边)下产生
      环;rowid 前置条件让"血缘只指向过去"成为物理事实。occurred_at 是
      秒精度不分先后,tiebreak 就是 rowid 本身(插入序 = 只增的物理含义)。
      注意 load_latest_snapshot 的 v.rowid 是 evidence 的,不可照抄。
    - 同 key 首跑(无更早集)查不到父,返回 None 不连边。
    - actor "system":血缘是客观派生事实,不带认识论立场(事实边)。
    - 竞态双 save 会产生两条不同 from_id 的边(各自指向自己的前一集),
      语义恰好正确;UNIQUE(from_type,from_id,kind,to_type,to_id) 是纯保险丝。
    """
    rows = await db.execute(
        "SELECT e.id FROM research_episodes e "
        "WHERE e.subject_type = ? AND e.subject_id = ? "
        "AND e.rowid < (SELECT rowid FROM research_episodes WHERE id = ?) "
        "ORDER BY e.rowid DESC LIMIT 1",
        (subject_type, subject_id, child_episode_id),
    )
    if not rows:
        return None
    parent_episode_id = str(rows[0]["id"])
    # 直接 INSERT 而非 ResearchEdgeRepository:edges.py import objects.py,
    # 反向引用会打结。校验语义与 add_fact_edge 等同(derived_from 已注册
    # 事实边;actor=system 合法;fact 类不带 rationale)。
    await db.execute(
        """
        INSERT INTO research_edges (
            id, from_type, from_id, to_type, to_id, kind, edge_class,
            rationale, provenance_json, actor, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'fact', NULL, '[]', 'system', ?)
        """,
        (
            new_id("edge"),
            "episode",
            child_episode_id,
            "episode",
            parent_episode_id,
            "derived_from",
            _now(),
        ),
    )
    return parent_episode_id


class EpisodeRepository:
    """世界中已经发生、有主体、有边界的一段经历。

    **刻意不提供 update / put / set / modify 任何写回方法**(§8③)。
    这不是遗漏:只要存在一个"更新 Episode"的入口,未来的 Agent 早晚会
    用它把历史改成"现在认为正确的样子"。让不可改成为 API 表面的事实,
    比写在文档里管用。
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def create(
        self,
        subject_type: str,
        subject_id: str,
        title: str,
        boundary_start: str,
        boundary_end: str,
        recorded: dict[str, Any] | None = None,
        occurred_at: str | None = None,
    ) -> str:
        episode_id = new_id("ep")
        now = _now()
        await self._db.execute(
            """
            INSERT INTO research_episodes (
                id, subject_type, subject_id, title, boundary_start, boundary_end,
                recorded_json, occurred_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                episode_id,
                subject_type,
                subject_id,
                title,
                boundary_start,
                boundary_end,
                _dump_json(recorded or {}),
                occurred_at or boundary_end,
                now,
            ),
        )
        return episode_id

    async def get(self, episode_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM research_episodes WHERE id = ?", (episode_id,)
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["recorded"] = _load_json(row.pop("recorded_json", None)) or {}
        return row

    async def list_episodes(
        self,
        subject_type: str | None = None,
        subject_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if subject_type is not None:
            clauses.append("subject_type = ?")
            params.append(subject_type)
        if subject_id is not None:
            clauses.append("subject_id = ?")
            params.append(subject_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = await self._db.execute(
            f"SELECT * FROM research_episodes {where} "
            "ORDER BY occurred_at DESC LIMIT ?",
            tuple(params),
        )
        result = []
        for r in rows:
            row = dict(r)
            row["recorded"] = _load_json(row.pop("recorded_json", None)) or {}
            result.append(row)
        return result

    async def list_evidence(self, episode_id: str) -> list[dict[str, Any]]:
        return await EvidenceRepository(self._db).list_for_episode(episode_id)

    async def record_lifecycle_transition(
        self,
        episode_id: str,
        to_status: str,
        *,
        actor: str = "system",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """流转生命周期状态(状态轴),返回流转后的 episode。

        **§8③ 的唯一有意例外**:这是 EpisodeRepository 全部公开方法中唯一
        允许写语义的方法,边界写死如下——
        - 只允许 ``recorded["lifecycle"]`` 子键 append-only 演化(旧 transition
          事件永不改,只追加);``recorded`` 顶层内容键(kind/snapshot_schema/
          n_rows/probe/...)永不改。
        - ``recorded["lifecycle"]`` 不存在的 episode(历史落账/无关实体)
          一律 raise,不静默补初始状态。
        - 流转必须命中 ALLOWED_TRANSITIONS;RETIRED 是终态,无出边。
        - to_status == "RETIRED" 时 reason 必填(终态不可逆,审计要求)。
        - lifecycle 是 per-episode 观察周期状态,非因子级状态;因子级状态
          由消费方从 derived_from 链推导。
        """
        if to_status not in LIFECYCLE_STATUSES:
            raise ValueError(f"unknown lifecycle status: {to_status!r}")
        if actor not in ("selene", "user", "system"):
            raise ValueError(f"unknown actor: {actor!r}")
        if to_status == "RETIRED" and not reason:
            raise ValueError("retiring an episode requires a reason")
        episode = await self.get(episode_id)
        if episode is None:
            raise KeyError(f"episode not found: {episode_id}")
        lifecycle = episode["recorded"].get("lifecycle")
        if not isinstance(lifecycle, dict) or "status" not in lifecycle:
            raise KeyError(
                f"episode {episode_id} has no lifecycle subkey; "
                "only ledger-written episodes (factor snapshot / probe) "
                "may transition"
            )
        from_status = lifecycle["status"]
        if from_status not in LIFECYCLE_STATUSES:
            raise ValueError(f"unknown lifecycle status: {from_status!r}")
        if to_status not in ALLOWED_TRANSITIONS[from_status]:
            raise ValueError(
                f"illegal lifecycle transition: {from_status!r} -> {to_status!r}"
            )
        transitions = lifecycle.get("transitions", [])
        transitions.append(
            {
                "from": from_status,
                "to": to_status,
                "at": _now(),
                "actor": actor,
                **({"reason": reason} if reason else {}),
            }
        )
        updated_recorded = dict(episode["recorded"])
        updated_recorded["lifecycle"] = {
            "status": to_status,
            "transitions": transitions,
        }
        await self._db.execute(
            "UPDATE research_episodes SET recorded_json = ? WHERE id = ?",
            (_dump_json(updated_recorded), episode_id),
        )
        result = await self.get(episode_id)
        assert result is not None
        return result


# ---------------------------------------------------------------------------
# Evidence —— 只增
# ---------------------------------------------------------------------------


class EvidenceRepository:
    """对现实/数据的可追溯观察。

    同一个 Episode 可以挂多条不同时点的观察 —— 这正是"当时 vs 现在"
    从 UI 问题变成数据模型问题的地方(§8③)。原 Episode 的 recorded 字段
    永远不被覆盖,新认识以新 Evidence 的形式出现。
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def create(
        self,
        episode_id: str,
        mode: str,
        payload: dict[str, Any] | None = None,
        source_ref: str = "",
        observed_at: str | None = None,
    ) -> str:
        if mode not in EVIDENCE_MODES:
            raise ValueError(f"unknown evidence mode: {mode!r}")
        episode = await self._db.execute(
            "SELECT 1 FROM research_episodes WHERE id = ? LIMIT 1", (episode_id,)
        )
        if not episode:
            raise KeyError(f"episode not found: {episode_id}")
        evidence_id = new_id("ev")
        now = _now()
        await self._db.execute(
            """
            INSERT INTO research_evidence (
                id, episode_id, mode, payload_json, source_ref, observed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                episode_id,
                mode,
                _dump_json(payload or {}),
                source_ref,
                observed_at or now,
                now,
            ),
        )
        return evidence_id

    async def get(self, evidence_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM research_evidence WHERE id = ?", (evidence_id,)
        )
        if not rows:
            return None
        return self._hydrate(dict(rows[0]))

    async def list_for_episode(self, episode_id: str) -> list[dict[str, Any]]:
        # 平手时用 rowid(插入序)收敛,不用 id。``_now()`` 是秒精度,同一个
        # Episode 上连开三条观察大概率落在同一秒 —— 那时 observed_at 全等,
        # 按 id 排就是按 ``uuid4`` 排,"当时 vs 现在"会随机颠倒:
        # compare_modes 的 rows[0] 可能取到 RECONSTRUCTED。
        # 插入序才是"只增"这件事的物理含义。
        rows = await self._db.execute(
            "SELECT * FROM research_evidence WHERE episode_id = ? "
            "ORDER BY observed_at ASC, rowid ASC",
            (episode_id,),
        )
        return [self._hydrate(dict(r)) for r in rows]

    async def compare_modes(
        self, episode_id: str, *, key: str | None = None
    ) -> dict[str, Any]:
        """当时 vs 现在。`drifted` 只看一个具体键是否变了,不做整体打分。"""
        rows = await self.list_for_episode(episode_id)
        if not rows:
            return {
                "episode_id": episode_id,
                "recorded": None,
                "latest": None,
                "drifted": False,
                "evidence_count": 0,
            }
        recorded = rows[0]["payload"]
        latest = rows[-1]["payload"]
        if key is not None:
            recorded_v, latest_v = recorded.get(key), latest.get(key)
        else:
            recorded_v, latest_v = recorded, latest
        return {
            "episode_id": episode_id,
            "recorded": recorded_v,
            "latest": latest_v,
            "drifted": recorded_v != latest_v,
            "evidence_count": len(rows),
        }

    @staticmethod
    def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
        row["payload"] = _load_json(row.pop("payload_json", None)) or {}
        return row


# ---------------------------------------------------------------------------
# Hypothesis —— 跨 Mission 存活
# ---------------------------------------------------------------------------


class HypothesisRepository:
    """针对问题的可检验解释。不挂在任何一个 Question 或 Mission 上(§6)。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def create(self, statement: str) -> str:
        hypothesis_id = new_id("hyp")
        now = _now()
        await self._db.execute(
            "INSERT INTO research_hypotheses (id, statement, status, created_at, updated_at) "
            "VALUES (?, ?, 'proposed', ?, ?)",
            (hypothesis_id, statement, now, now),
        )
        return hypothesis_id

    async def get(self, hypothesis_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM research_hypotheses WHERE id = ?", (hypothesis_id,)
        )
        return dict(rows[0]) if rows else None

    async def set_status(self, hypothesis_id: str, status: str) -> None:
        if status not in HYPOTHESIS_STATUSES:
            raise ValueError(f"unknown hypothesis status: {status!r}")
        existing = await self.get(hypothesis_id)
        if existing is None:
            raise KeyError(f"hypothesis not found: {hypothesis_id}")
        await self._db.execute(
            "UPDATE research_hypotheses SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), hypothesis_id),
        )

    async def list_hypotheses(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if status is None:
            rows = await self._db.execute(
                "SELECT * FROM research_hypotheses ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        else:
            rows = await self._db.execute(
                "SELECT * FROM research_hypotheses WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Experiment —— 跨 Mission 存活
# ---------------------------------------------------------------------------


class ExperimentRepository:
    """对假设进行验证的可复现行动。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    @property
    def db(self) -> Any:
        """公开连接句柄:跨包组合(如 ErrorReplayInjector)免碰私有。"""
        return self._db

    async def create(
        self,
        kind: str,
        hypothesis_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> str:
        experiment_id = new_id("exp")
        await self._db.execute(
            """
            INSERT INTO research_experiments (
                id, hypothesis_id, kind, config_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (experiment_id, hypothesis_id, kind, _dump_json(config or {}), _now()),
        )
        return experiment_id

    async def get(self, experiment_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM research_experiments WHERE id = ?", (experiment_id,)
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["config"] = _load_json(row.pop("config_json", None)) or {}
        return row

    async def mark_ran(self, experiment_id: str, ran_at: str | None = None) -> None:
        existing = await self.get(experiment_id)
        if existing is None:
            raise KeyError(f"experiment not found: {experiment_id}")
        if existing["ran_at"] is not None:
            raise ValueError(f"experiment already ran: {experiment_id}")
        await self._db.execute(
            "UPDATE research_experiments SET ran_at = ? WHERE id = ?",
            (ran_at or _now(), experiment_id),
        )

    async def set_outcome(
        self,
        experiment_id: str,
        outcome: str,
        error_ids: list[str] | None = None,
    ) -> None:
        """落定实验成败并关联错误类型。

        失败即写(rd-agent 要等任务成功才把失败链固化进图,
        放弃史会被彻底丢弃 —— 这里不等,20 连败的错误沉淀也在)。
        """
        if outcome not in ("success", "failed"):
            raise ValueError(f"outcome must be 'success' or 'failed': {outcome!r}")
        if outcome == "success" and error_ids:
            raise ValueError("success rows carry no error ids")
        existing = await self.get(experiment_id)
        if existing is None:
            raise KeyError(f"experiment not found: {experiment_id}")
        await self._db.execute(
            "UPDATE research_experiments "
            "SET outcome = ?, error_ids_json = ? WHERE id = ?",
            (outcome, _dump_json(error_ids or []), experiment_id),
        )

    async def count_outcome(self, hypothesis_id: str, outcome: str) -> int:
        rows = await self._db.execute(
            "SELECT COUNT(*) AS n FROM research_experiments "
            "WHERE hypothesis_id = ? AND outcome = ?",
            (hypothesis_id, outcome),
        )
        return int(rows[0]["n"]) if rows else 0

    async def list_experiments(
        self, hypothesis_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if hypothesis_id is None:
            rows = await self._db.execute(
                "SELECT * FROM research_experiments ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        else:
            rows = await self._db.execute(
                "SELECT * FROM research_experiments WHERE hypothesis_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (hypothesis_id, limit),
            )
        result = []
        for r in rows:
            row = dict(r)
            row["config"] = _load_json(row.pop("config_json", None)) or {}
            result.append(row)
        return result


# ---------------------------------------------------------------------------
# Decision —— 归 Director
# ---------------------------------------------------------------------------


class DecisionRepository:
    """Director 对研究结果作出的正式判断(§6)。

    ``record`` 不接受 ``decided_by`` 参数,恒写入 ``'director'`` —— Mission
    只能产出 Findings / Recommendation / Candidate Decision,正式判断没有
    让 AI 代劳的入口。这是设计,不是参数省略。
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def record(
        self,
        question_id: str,
        verdict: str,
        *,
        rationale: str = "",
        window_start: str | None = None,
        window_end: str | None = None,
    ) -> str:
        decision_id = new_id("dec")
        await self._db.execute(
            """
            INSERT INTO research_decisions (
                id, question_id, verdict, rationale, decided_by,
                outcome_status, window_start, window_end, decided_at
            ) VALUES (?, ?, ?, ?, 'director', ?, ?, ?, ?)
            """,
            (
                decision_id,
                question_id,
                verdict,
                rationale,
                OUTCOME_PENDING,
                window_start,
                window_end,
                _now(),
            ),
        )
        return decision_id

    async def get(self, decision_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM research_decisions WHERE id = ?", (decision_id,)
        )
        return dict(rows[0]) if rows else None

    async def observe_outcome(self, decision_id: str) -> dict[str, Any]:
        """Decision 之后发生了什么。Episode 将成为它的 Outcome(§8④ 语义角色)。"""
        decision = await self.get(decision_id)
        if decision is None:
            raise KeyError(f"decision not found: {decision_id}")
        if decision["outcome_status"] != OUTCOME_PENDING:
            raise ValueError(
                f"outcome already resolved: {decision['outcome_status']!r}"
            )
        # pending -> resolved is one-shot; the WHERE guard makes a concurrent
        # double-resolve a no-op instead of a silent overwrite.
        await self._db.execute(
            "UPDATE research_decisions SET outcome_status = ? "
            "WHERE id = ? AND outcome_status = ?",
            (OUTCOME_OBSERVED, decision_id, OUTCOME_PENDING),
        )
        updated = await self.get(decision_id)
        assert updated is not None
        if updated["outcome_status"] != OUTCOME_OBSERVED:
            raise ValueError(f"outcome already resolved: {updated['outcome_status']!r}")
        return updated

    async def mark_never(
        self, decision_id: str, *, window_end: str | None = None
    ) -> dict[str, Any]:
        """§8④ 的 never 防线。

        `never` 的准确含义是"**该 Decision 定义的结果事件在明确观察窗口内
        未发生**",不是"Decision 没结果"。所以必须传窗口,且窗口必须已经
        走完 —— 否则 Agent 会把"没等够"(仍是 pending)和"永远不会发生"
        混为一谈,§4 的"正确的不行动"就守不住了。
        """
        decision = await self.get(decision_id)
        if decision is None:
            raise KeyError(f"decision not found: {decision_id}")
        if decision["outcome_status"] != OUTCOME_PENDING:
            raise ValueError(
                f"outcome already resolved: {decision['outcome_status']!r}"
            )
        effective_end = window_end or decision["window_end"]
        if not effective_end:
            raise ValueError(
                "outcome_never requires an explicit observation window — "
                "'not yet' is not 'never'"
            )
        if not _is_past(effective_end):
            raise ValueError(
                f"observation window is still open until {effective_end} — "
                "outcome must stay outcome_pending"
            )
        # One-shot pending -> never; WHERE guard against concurrent resolve.
        await self._db.execute(
            "UPDATE research_decisions SET outcome_status = ?, window_end = ? "
            "WHERE id = ? AND outcome_status = ?",
            (OUTCOME_NEVER, effective_end, decision_id, OUTCOME_PENDING),
        )
        updated = await self.get(decision_id)
        assert updated is not None
        if updated["outcome_status"] != OUTCOME_NEVER:
            raise ValueError(f"outcome already resolved: {updated['outcome_status']!r}")
        return updated

    async def list_decisions(
        self, question_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if question_id is None:
            rows = await self._db.execute(
                "SELECT * FROM research_decisions ORDER BY decided_at DESC LIMIT ?",
                (limit,),
            )
        else:
            rows = await self._db.execute(
                "SELECT * FROM research_decisions WHERE question_id = ? "
                "ORDER BY decided_at DESC LIMIT ?",
                (question_id, limit),
            )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# ErrorType —— 失败经验回放的错误字典(v017)


class ErrorTypeRepository:
    """错误类型字典:content 精确匹配去重,等价 rd-agent error 节点的复用机制。

    id 由 content 哈希派生,同一错误文本天然同一行 —— upsert 只累加计数。
    layer 是 CoSTEER 三层分类(v018):undefined → 具体层单向升级,
    已具体层不回退(回退 = 分析器抖动,不采信)。
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    @staticmethod
    def make_id(content: str) -> str:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        return f"err-{digest}"

    async def upsert(self, content: str, *, layer: str = "undefined") -> str:
        """写入或累加一种错误,返回其 id。失败即写,不等成功。

        层升级规则:已有行层为 undefined → 取新层;已具体层恒保持。
        """
        if layer not in ERROR_LAYERS:
            raise ValueError(f"unknown error layer: {layer!r}")
        error_id = self.make_id(content)
        now = _now()
        await self._db.execute(
            """
            INSERT INTO research_error_types (id, content, first_seen_at, occurrence_count, layer)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(content) DO UPDATE SET
                occurrence_count = occurrence_count + 1,
                layer = CASE
                    WHEN research_error_types.layer = 'undefined' THEN excluded.layer
                    ELSE research_error_types.layer
                END
            """,
            (error_id, content, now, layer),
        )
        return error_id

    async def get(self, error_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM research_error_types WHERE id = ?", (error_id,)
        )
        return dict(rows[0]) if rows else None

    async def list_all(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._db.execute(
            "SELECT * FROM research_error_types "
            "ORDER BY occurrence_count DESC, first_seen_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

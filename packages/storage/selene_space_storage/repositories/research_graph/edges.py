"""关系边仓储 —— 研究图谱的骨架。

一条分界决定全部分类:**每条边要么是事实,要么是判断,没有第三种**(§7)。

- 事实边(derived_from / addresses / observed_from / ...):系统自动可加,
  描述已经发生了什么,不带认识论立场
- 判断边(supports / contradicts / qualifies / applies_to):**必须**携带
  推理依据 + 出处 + 谁做的(D-3)。系统可以提议,Director 可以覆盖
- 对称边(relates_to):只存一份,规范化顺序,否则引用迁移统计会算重

两条不许破的线:
- D-4 图不得自动去矛盾。同一证据同时 supports H1 / contradicts H2 是正常状态,
  矛盾由 Revision / Decision 解释。图强制自洽 = 静默改写历史 = 撞 C-4
- D-5 撤销最多一层。撤销是**追加**一条 revokes 边,原边永远留在历史里;
  但 revokes 边本身不可再被撤销 —— 更深的审计信息另建结构,不靠边套边
"""

from __future__ import annotations

from typing import Any

from selene_space_storage.repositories.research_graph._shared import (
    ALL_EDGE_KINDS,
    FACT_EDGE_KINDS,
    JUDGMENT_EDGE_KINDS,
    SYMMETRIC_EDGE_KINDS,
    _dump_json,
    _load_json,
    _now,
    canonical_pair,
    new_id,
)

Ref = tuple[str, str]

_VALID_ACTORS = ("selene", "user", "system")
# ↑ 三词词表与 DB 的 CHECK 约束同源。**它是"谁写的这一笔",不是"谁有这个权限"。**
#   Director 是**角色**,记在 research_decisions.decided_by(那列只有一个合法值
#   'director',所以"AI 冒充 Director"在 schema 层不可能)。两轴不要互相借词:
#   往这里加 'director' 会同时撞 DB CHECK,"系统提议的撤销记谁"也会变成无解。


class ResearchEdgeRepository:
    """Facade over ``research_edges``."""

    def __init__(self, db: Any) -> None:
        self._db = db

    # -- 事实边 -------------------------------------------------------------

    async def add_fact_edge(
        self,
        from_ref: Ref,
        to_ref: Ref,
        kind: str,
        *,
        actor: str = "system",
    ) -> dict[str, Any]:
        """系统自动记录的事实关联。判断类 kind 走这条路会被直接拒绝(D-3)。"""
        if kind in JUDGMENT_EDGE_KINDS:
            raise ValueError(
                f"{kind!r} is a judgment kind — use add_judgment_edge with rationale"
            )
        if kind not in FACT_EDGE_KINDS:
            raise ValueError(f"unregistered edge kind: {kind!r}")
        return await self._insert(
            from_ref,
            to_ref,
            kind,
            edge_class="fact",
            actor=actor,
            rationale=None,
            provenance=[],
        )

    # -- 判断边 -------------------------------------------------------------

    async def add_judgment_edge(
        self,
        from_ref: Ref,
        to_ref: Ref,
        kind: str,
        *,
        rationale: str,
        provenance: list[str],
        actor: str = "selene",
    ) -> dict[str, Any]:
        """认识论判断。没有推理依据和出处就不许入账 —— 不然后人无法审计。"""
        if kind not in JUDGMENT_EDGE_KINDS:
            raise ValueError(f"{kind!r} is not a judgment kind")
        if not rationale or not rationale.strip():
            raise ValueError("judgment edge requires a non-empty rationale")
        if not provenance:
            raise ValueError("judgment edge requires at least one provenance ref")
        return await self._insert(
            from_ref,
            to_ref,
            kind,
            edge_class="judgment",
            actor=actor,
            rationale=rationale,
            provenance=list(provenance),
        )

    # -- 对称边 -------------------------------------------------------------

    async def relate(
        self, a_ref: Ref, b_ref: Ref, *, actor: str = "system"
    ) -> dict[str, Any]:
        left, right = canonical_pair(a_ref, b_ref)
        if left == right:
            raise ValueError("relates_to cannot be a self loop")
        return await self._insert(
            left,
            right,
            "relates_to",
            edge_class="fact",
            actor=actor,
            rationale=None,
            provenance=[],
        )

    # -- 撤销 ---------------------------------------------------------------

    async def revoke(
        self, edge_id: str, *, actor: str = "user", reason: str
    ) -> dict[str, Any]:
        """撤销一条边 —— 追加 revokes 边,不删原边(C-4 只增不改)。

        默认 ``actor="user"``:**撤销判断边是 Director 的动作**(§5「系统可提,
        Director 可覆盖」),而在存储层"人"这个名字就是 ``user``。

        ⚠️ **不要写成 ``actor="director"``** —— ``actor`` 这一列的值域是三词
        ``('selene','user','system')``,``_insert`` 会直接抛 ``ValueError``。
        "Director" 是**角色**,记在 ``research_decisions.decided_by`` 那个列上,
        不是 ``actor`` 这一轴。两轴混用会让"系统提议的撤销该记谁"变成无解。

        D-5:最多一层。撤销一条 revokes 边会被拒绝,因为那意味着"撤销的撤销",
        指向的其实是审计结构,不该由关系边承担。
        """
        target = await self._get_edge(edge_id)
        if target is None:
            raise KeyError(f"edge not found: {edge_id}")
        if target["kind"] == "revokes":
            raise ValueError(
                "revocation is not recursive — a revokes edge cannot be revoked"
            )
        if await self.is_revoked(edge_id):
            raise ValueError(f"edge already revoked: {edge_id}")
        return await self._insert(
            ("edge", edge_id),
            ("edge", edge_id),
            "revokes",
            edge_class="fact",
            actor=actor,
            rationale=reason or None,
            provenance=[],
        )

    async def is_revoked(self, edge_id: str) -> bool:
        rows = await self._db.execute(
            "SELECT 1 FROM research_edges "
            "WHERE kind = 'revokes' AND from_type = 'edge' AND from_id = ? LIMIT 1",
            (edge_id,),
        )
        return bool(rows)

    # -- 查询 ---------------------------------------------------------------

    async def list_edges(
        self,
        node_id: str | None = None,
        *,
        direction: str = "both",
        kind: str | None = None,
        include_revoked: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if node_id is not None:
            if direction == "out":
                clauses.append("from_id = ?")
                params.append(node_id)
            elif direction == "in":
                clauses.append("to_id = ?")
                params.append(node_id)
            else:
                clauses.append("(from_id = ? OR to_id = ?)")
                params.extend([node_id, node_id])
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if not include_revoked:
            # 撤销是**全局可见性**,不是这一页里的局部关系。revokes 边挂在被撤销
            # 边的 **id** 上(from_id = to_id = edge id),不挂在它两个端点上 ——
            # 所以按节点查出来的任何一页里都不会有那条 revokes 边,在 Python 侧
            # 过滤等于没过滤(实测:撤销后 exists_between 仍返回 True)。
            clauses.append(
                "id NOT IN (SELECT to_id FROM research_edges WHERE kind = 'revokes')"
            )
        params.append(limit)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._db.execute(
            f"SELECT * FROM research_edges {where} ORDER BY created_at ASC LIMIT ?",
            tuple(params),
        )
        return [self._hydrate(dict(r)) for r in rows]

    async def neighbors(self, node_id: str, *, kind: str | None = None) -> list[str]:
        result: list[str] = []
        for edge in await self.list_edges(node_id, kind=kind):
            other = edge["to_id"] if edge["from_id"] == node_id else edge["from_id"]
            if other != node_id:
                result.append(other)
        return sorted(set(result))

    async def exists_between(self, a_id: str, b_id: str) -> bool:
        """任意方向、任意 kind、且未被撤销 —— 引用的连通性判据用这个。"""
        for edge in await self.list_edges(a_id):
            if {edge["from_id"], edge["to_id"]} == {a_id, b_id} and a_id != b_id:
                return True
        return False

    # -- 内部 ---------------------------------------------------------------

    async def _insert(
        self,
        from_ref: Ref,
        to_ref: Ref,
        kind: str,
        *,
        edge_class: str,
        actor: str,
        rationale: str | None,
        provenance: list[str],
    ) -> dict[str, Any]:
        if actor not in _VALID_ACTORS:
            raise ValueError(f"unknown actor: {actor!r}")
        if kind not in ALL_EDGE_KINDS:
            raise ValueError(f"unregistered edge kind: {kind!r}")
        if kind in SYMMETRIC_EDGE_KINDS:
            from_ref, to_ref = canonical_pair(from_ref, to_ref)
        edge_id = new_id("edge")
        await self._db.execute(
            """
            INSERT INTO research_edges (
                id, from_type, from_id, to_type, to_id, kind, edge_class,
                rationale, provenance_json, actor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge_id,
                from_ref[0],
                from_ref[1],
                to_ref[0],
                to_ref[1],
                kind,
                edge_class,
                rationale,
                _dump_json(provenance),
                actor,
                _now(),
            ),
        )
        row = await self._get_edge(edge_id)
        assert row is not None
        return row

    async def _get_edge(self, edge_id: str) -> dict[str, Any] | None:
        rows = await self._db.execute(
            "SELECT * FROM research_edges WHERE id = ?", (edge_id,)
        )
        if not rows:
            return None
        return self._hydrate(dict(rows[0]))

    @staticmethod
    def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
        row["provenance"] = _load_json(row.pop("provenance_json", None)) or []
        return row

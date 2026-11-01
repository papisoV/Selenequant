"""关系读取的唯一入口:边表 ∪ 列派生。

**为什么需要它**:同一关系在系统里有两处载体,历史上各消费者只读其中一半 ——

- ``research_edges`` 表:承载**没有列可以表达**的关系
  (`derived_from` episode→episode 同表无列;
   `addresses` evidence→question 跨表无列)
- **列**:承载**列本身已经表达**的关系
  (``research_revisions.question_id`` / ``research_evidence.episode_id``,
   两列都是 NOT NULL 外键 —— 行存在即关系存在)

若各读一半,同一张图在不同消费者眼里就是不同的图:``/view`` 只读边表,
30 条列关系对它不可见,前端只好自己补一遍。本模块给出并集。

**约定(见 ``_shared.py`` 注册表注释,勿违)**:
列能承载的关系**不写边**。给这类关系补边 = 制造第二真相源,
一旦两边漂移就再也说不清哪个对。所以本模块只**读**,不改任何写入方,
也不回填历史边。

派生的关系行 ``id`` 用 ``derived:`` 前缀,与真边 id(``edge-`` 开头,
由 ``new_id`` 铸造)**不会撞** —— 真边 id 要能被 ``revokes`` 寻址,
必须保持唯一命名空间。
"""

from __future__ import annotations

from typing import Any

# 列派生关系的 kind 名。与 ``_shared.py`` 注册表里的取值一致 ——
# 那里注册的是"这个关系是什么",这里给的是"从列读出来时叫什么"。
_REVISION_OF = "revision_of"
_OBSERVED_FROM = "observed_from"

# 派生行 id 前缀。真边 id 形如 ``edge-<hex12>``,不会与本前缀相撞。
_DERIVED_PREFIX = "derived:"


async def list_relations(db: Any) -> list[dict[str, Any]]:
    """返回全部关系 = ``research_edges`` 全部行 + 列派生关系。

    每行形状与 ``research_edges`` 投影一致,另带 ``derived`` 标记:

    - ``derived=False`` —— 真边,有可寻址的 ``id``(``edge-...``)
    - ``derived=True``  —— 从列读出,``id`` 为 ``derived:<对象 id>``,
      只在这个并集里有意义,不可拿去 ``revokes``

    调用方若只关心"关系本身"(画图、算邻居、判连通),两者一视同仁;
    若要寻址某条边(撤销、加判断依据),必须过滤 ``derived`` 为假。
    """
    out: list[dict[str, Any]] = []

    for row in await db.execute(
        "SELECT id, from_type, from_id, to_type, to_id, kind FROM research_edges"
    ):
        out.append({**dict(row), "derived": False})

    for row in await db.execute(
        "SELECT id, question_id FROM research_revisions "
        "WHERE question_id IS NOT NULL AND question_id != ''"
    ):
        out.append(
            {
                "id": f"{_DERIVED_PREFIX}{row['id']}",
                "from_type": "revision",
                "from_id": row["id"],
                "to_type": "question",
                "to_id": row["question_id"],
                "kind": _REVISION_OF,
                "derived": True,
            }
        )

    for row in await db.execute(
        "SELECT id, episode_id FROM research_evidence "
        "WHERE episode_id IS NOT NULL AND episode_id != ''"
    ):
        out.append(
            {
                "id": f"{_DERIVED_PREFIX}{row['id']}",
                "from_type": "evidence",
                "from_id": row["id"],
                "to_type": "episode",
                "to_id": row["episode_id"],
                "kind": _OBSERVED_FROM,
                "derived": True,
            }
        )

    return out

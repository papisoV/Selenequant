"""召回 —— §1④ 分界句唯一的物理载体。

判据:**Selene 让上一段历史改变下一次判断。** 只有保存没有召回,
名字叫 Research Archive(§14 #6)—— 而 Obsidian + Git 也能保存。

三件事:

1. **取回** —— 给定 Anchor + 当前研究上下文,从图里找出候选结论。
   触发键是"**当前研究状态与历史情境相似**",不是"同一个 Question"
   (§12.1 第 14 行)。跨 Question 召回是这个函数存在的理由之一。
2. **分层** —— 证据链走到 Episode / Evidence 的 = ``LEARNED_EXPERIENCE``;
   走不到的 = ``RESEARCH_RECORD``(§6.10 ⑤)。**两者都能进下一次判断,
   但身份不同** —— 后者只能当"待检验线索",不得被当作既有知识消费。
3. **可审计** —— 每个候选都带 ``matched_on``(为什么被取回)。v0 纪律:
   不上 embedding、不造假精度;匹配不上的宁可不召回。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from ._shared import _load_json, _parse_iso
from .edges import ResearchEdgeRepository

# -- 可召回的对象类型 -------------------------------------------------------
REVISION = "revision"
HYPOTHESIS = "hypothesis"
EVIDENCE = "evidence"

# -- 召回分层(§6.10 ⑤):历史存在 ≠ 历史有效 ------------------------------
LEARNED_EXPERIENCE = "learned_experience"
RESEARCH_RECORD = "research_record"

# 证据链回溯的最大跳数。revision → cited 对象 → evidence 已经是 2 跳,
# 留一跳余量;再深就是在猜,不如老实标成 Research Record。
_MAX_CHAIN_HOPS = 3

_TABLE_FOR_KIND = {
    "revision": "research_revisions",
    "hypothesis": "research_hypotheses",
    "evidence": "research_evidence",
}

# 前缀 → 类型。ep-/ev- 不冲突(第三个字符分别是 p / v,而 exp- 是 x)。
_PREFIX_FOR_KIND = (
    ("rev-", "revision"),
    ("hyp-", "hypothesis"),
    ("exp-", "experiment"),
    ("dec-", "decision"),
    ("ep-", "episode"),
    ("ev-", "evidence"),
    ("q-", "question"),
)

# 证据链的终点:能走到这两类对象,历史就算被现实 / 数据锚住了
_CHAIN_TERMINALS = frozenset({"evidence", "episode"})

# **只有这几条边算"证据链"**。``addresses`` 刻意不在里面 ——
# 它是"进了这个问题的研究世界"的凭证,不是"被现实锚住了"。
# 这条区分不写死,任何一个问题下的假设都会因为同问题下有别的证据
# 而被误判成 Learned Experience。
_EVIDENCE_EDGE_KINDS = frozenset({"supports", "contradicts", "qualifies"})

# 已被证伪 / 已退役的假设:历史有效 ≠ 当前有效。仍然召回(失败史有复利
# 价值),但标 superseded 并降权 —— 不许和活假设同分排序。
_SUPERSEDED_HYPOTHESIS_STATUSES = frozenset({"refuted", "retired"})
_SUPERSEDED_PENALTY = 1

_ANCHOR_KEYS = ("strategy_ids", "intervals", "regimes")

# evidence.payload_json 的可读文本键,按优先级。真库实测 13 条 payload 有
# 三种形状:probe 写 ``summary``、分析写 ``conclusion``(+verdict/key_numbers)、
# 行情快照写 ``snapshot_schema``+``rows``(单条 124KB)。
# 只认 summary 会让后两种整坨 JSON 进 prompt —— 实测 11 条候选里 4 条如此,
# 且都落在 learned_experience(最高价值层)。
_EVIDENCE_TEXT_KEYS = ("summary", "conclusion", "statement", "note", "title")

# 兜底截断:取不到任何可读键时,整坨 JSON 最多这么多字符进 prompt。
# 124KB 快照淹掉整个账本段,LLM 既读不懂又挤掉真内容。
_EVIDENCE_TEXT_MAX = 400

# B1 purpose 预设:召回意图的分光焊点。kinds/require_class 的默认值来源,
# 不是第三个过滤维度 —— 视角细分是 investigate 会内部设计,不进这层。
# 解析顺序(见 recall()):显式参数 > purpose preset > 旧默认。
_PURPOSE_PRESETS: dict[str, dict[str, Any]] = {
    "profile_generation": {"kinds": (REVISION,), "require_class": None},
    "investigate": {"kinds": (REVISION, HYPOTHESIS, EVIDENCE), "require_class": None},
    "hypothesis_challenge": {"kinds": (HYPOTHESIS,), "require_class": None},
}


def _kind_of(node_id: str) -> str:
    for prefix, kind in _PREFIX_FOR_KIND:
        if node_id.startswith(prefix):
            return kind
    return "unknown"


def _evidence_text(payload: dict[str, Any], raw: str) -> str:
    """evidence payload → 可读单行文本(喂 prompt 用)。

    优先级:可读键 > 结构化兜底摘要 > 截断的原始 JSON。
    **绝不把整坨 JSON 喂进 prompt** —— 真库有 124KB 的行情快照,
    它既读不懂又挤掉账本段里真正有信息量的候选。
    """
    for key in _EVIDENCE_TEXT_KEYS:
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()

    # 无任何可读键:用结构化字段拼一行摘要,比裸 JSON 有用得多
    bits: list[str] = []
    probe = payload.get("probe")
    if isinstance(probe, str) and probe:
        bits.append(probe)
    params = payload.get("params")
    if isinstance(params, dict):
        for k in ("factor", "interval", "market_type"):
            if params.get(k):
                bits.append(str(params[k]))
        syms = params.get("symbols")
        if isinstance(syms, list) and syms:
            bits.append(f"{len(syms)}标的")
    boundary = payload.get("boundary")
    if isinstance(boundary, dict) and boundary.get("start"):
        bits.append(f"{boundary['start']}~{boundary.get('end') or '?'}")
    if bits:
        return "行情快照 " + " · ".join(bits)

    text = (
        raw if len(raw) <= _EVIDENCE_TEXT_MAX else raw[:_EVIDENCE_TEXT_MAX] + "…(截断)"
    )
    return text


async def _anchor_of(db: Any, question_id: str) -> dict[str, Any]:
    rows = await db.execute(
        "SELECT anchor_json FROM research_questions WHERE id = ?", (question_id,)
    )
    if not rows:
        return {}
    return _load_json(rows[0]["anchor_json"]) or {}


def _anchor_overlap(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """返回重叠的维度名。可解释、可审计 —— 这是"相似"在 v0 的全部含义。"""
    hit: list[str] = []
    for key in _ANCHOR_KEYS:
        left = set(a.get(key) or ())
        right = set(b.get(key) or ())
        if left & right:
            hit.append(f"anchor:{key}")
    return hit


async def _cited_of(db: Any, revision_id: str) -> list[str]:
    rows = await db.execute(
        "SELECT cited_json FROM research_revisions WHERE id = ?", (revision_id,)
    )
    if not rows:
        return []
    return list(_load_json(rows[0]["cited_json"]) or [])


async def _question_ids_of(db: Any, node_id: str) -> list[str]:
    """对象挂在哪些问题下(任一方向)。假设是跨问题存活的,所以这里是复数。"""
    rows = await db.execute(
        "SELECT from_type, from_id, to_id FROM research_edges "
        "WHERE (from_type = 'question' AND to_id = ?) "
        "   OR (to_type = 'question' AND from_id = ?)",
        (node_id, node_id),
    )
    out: list[str] = []
    for row in rows:
        out.append(row["from_id"] if row["from_type"] == "question" else row["to_id"])
    return sorted(set(out))


async def _chain_seeds(db: Any, kind: str, node_id: str) -> list[str]:
    """下一步该往哪走。**只有两条路径算证据链。**

    - ``revision`` → 它 ``cited`` 的对象(这一版回答引用过谁)
    - ``hypothesis`` → 它身上 supports / contradicts / qualifies 的 Evidence
    """
    if kind == "revision":
        return await _cited_of(db, node_id)
    if kind == "hypothesis":
        seeds: list[str] = []
        for edge in await ResearchEdgeRepository(db).list_edges(node_id):
            if edge["kind"] not in _EVIDENCE_EDGE_KINDS:
                continue
            if edge["from_id"] == node_id and edge["to_type"] == "evidence":
                seeds.append(edge["to_id"])
            elif edge["to_id"] == node_id and edge["from_type"] == "evidence":
                seeds.append(edge["from_id"])
        return sorted(set(seeds))
    return []


async def _evidence_chain(db: Any, kind: str, node_id: str) -> list[str]:
    """从 node 往回走,找一条到 Evidence / Episode 的路。

    走不到返回 ``[]``。**"走不走得到"是可机械检查的** —— §7.4 从纪律
    变成能自动验的东西,就在这个函数。
    """
    if kind in _CHAIN_TERMINALS:
        chain = [f"{kind}:{node_id}"]
        if kind == "evidence":
            rows = await db.execute(
                "SELECT episode_id FROM research_evidence WHERE id = ?", (node_id,)
            )
            if rows:
                chain.append(f"episode:{rows[0]['episode_id']}")
        return chain

    seen: set[tuple[str, str]] = {(kind, node_id)}
    frontier: list[tuple[str, str, list[str]]] = [(kind, node_id, [])]

    while frontier:
        cur_kind, cur_id, path = frontier.pop(0)
        if len(path) >= _MAX_CHAIN_HOPS:
            continue
        for seed in await _chain_seeds(db, cur_kind, cur_id):
            seed_kind = _kind_of(seed)
            if seed_kind in _CHAIN_TERMINALS:
                return [f"{kind}:{node_id}"] + [*path, f"{seed_kind}:{seed}"]
            node = (seed_kind, seed)
            if node in seen or seed_kind == "unknown":
                continue
            seen.add(node)
            frontier.append((seed_kind, seed, [*path, f"{seed_kind}:{seed}"]))
    return []


async def _load_candidates(db: Any, kind: str) -> list[dict[str, Any]]:
    """载入某类候选。

    ``ponytail:`` 全表扫。v0 的库是单用户、量级是几百条,足够;
    上万条再考虑按 anchor 建索引表 —— 现在建是给不存在的规模买单。
    """
    table = _TABLE_FOR_KIND[kind]
    if kind == "revision":
        # horizon(v025)随召回物透传:tactical 的寿命语义与 structural 不同,
        # 消费侧据此降权/标注;hypothesis/evidence 无此列,不透传。
        rows = await db.execute(
            f"SELECT id, question_id, content AS text, horizon, created_at FROM {table}"
        )
    elif kind == "evidence":
        # probe 类观察没有 question_id 列;payload 取可读文本(summary /
        # conclusion / 结构化兜底),不再把整坨 JSON 当文本。status=None
        # 让 superseded 逻辑天然跳过。
        # LEFT JOIN episode 带出数据窗(boundary_start/end)——仅 as_of 回放
        # 模式消费,见 recall();内层键不进返回 dict,默认路径逐键不变。
        rows = await db.execute(
            f"SELECT e.id, NULL AS question_id, e.payload_json, e.created_at, "
            f"ep.boundary_start AS ep_boundary_start, "
            f"ep.boundary_end AS ep_boundary_end "
            f"FROM {table} e "
            f"LEFT JOIN research_episodes ep ON ep.id = e.episode_id"
        )
        out = []
        for row in rows:
            d = dict(row)
            raw = d.pop("payload_json", None) or "{}"
            payload = _load_json(raw) or {}
            d["text"] = _evidence_text(payload, raw)
            d["status"] = None
            out.append(d)
        return out
    else:
        rows = await db.execute(
            f"SELECT id, NULL AS question_id, statement AS text, status, created_at "
            f"FROM {table}"
        )
    return [dict(row) for row in rows]


def parse_as_of(as_of: str) -> datetime:
    """校验并解析 as_of(账本回放的认识论边界),非法即抛。

    公开给消费方(council / 端点)复用同一校验语义:解析不出 →
    ``ValueError``(调用方的错,不静默降级成"无过滤"—— 那会返回比
    用户预期更多的数据且毫无提示)。``_parse_iso`` 的吞错语义是
    "无从判断"专用,这里显式区分。
    """
    ts = _parse_iso(as_of)
    if ts is None:
        raise ValueError(f"invalid as_of (expect ISO 8601): {as_of!r}")
    return ts


def filter_known_by(
    items: Sequence[dict[str, Any]], as_of_ts: datetime, *, key: str = "created_at"
) -> list[dict[str, Any]]:
    """按"as_of 时刻已知"过滤字典列表:``items[i][key] <= as_of_ts`` 保留。

    "as_of 时刻已知"的统一定义收敛在这里(recall 候选过滤与 council
    回答链过滤共用,消除 parse/过滤逻辑漂移面):时间戳解析不出 →
    排除(无法断言"当时已知",不静默收入);解析成功且 > as_of → 排除。
    """
    out: list[dict[str, Any]] = []
    for item in items:
        ts = _parse_iso(item.get(key))
        if ts is None or ts > as_of_ts:
            continue
        out.append(item)
    return out


async def recall(
    db: Any,
    *,
    anchor: dict[str, Any] | None = None,
    subject: tuple[str, str] | None = None,
    question_id: str | None = None,
    kinds: Sequence[str] | None = None,
    require_class: str | None = None,
    limit: int = 20,
    purpose: str | None = None,
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    """取回候选结论。返回项里的 ``id`` 可直接喂给 ``add_revision(cited=[...])``。

    ``purpose`` 声明召回意图(B1),从 ``_PURPOSE_PRESETS`` 取 kinds/require_class
    默认值;解析顺序 = 显式参数 > preset > 旧默认 ``(REVISION, HYPOTHESIS)``。
    未知 purpose 直接 ValueError(fail fast)。

    ``as_of``(账本回放)按**认识论**语义过滤:只保留 ``created_at <= as_of``
    的候选 —— 即"账本在那一刻已经知道的东西"。``as_of`` 之后才记录的候选
    一律不出现(前视排除,F3 家族纪律)。

    ⚠️ **两条边界(消费方必读)**:

    1. **保证不前视,但不保证完整召回**。可见集同时受当前图状态的 matched
       门约束(归属边 + anchor 重叠,见下方循环)—— 一条 ``as_of`` 之前记录、
       但**今天才连上** question 边的 evidence,回放时会"出现";当时存在但
       从未连边、或 owner anchor 与当前 question 不重叠的候选,回放时"不出现"。
       单表无历史,无法重建"当时的边/anchor",此为 v0 固有局限。
    2. **属性是当前态**。``status`` / ``superseded`` / ``evidence_chain`` /
       ``recall_class`` 均按**当前**图状态计算,不是 as_of 时刻的状态。

    ``as_of`` 为 None 时走与原实现完全相同的路径(零行为变更)。
    日粒度回放按 **UTC 日终**;跨时区存在至多一个时区偏移的边界模糊。
    """
    if purpose is not None and purpose not in _PURPOSE_PRESETS:
        raise ValueError(f"unknown purpose: {purpose!r}")
    # fail-fast:非法 as_of 是调用方的错,不静默降级成"无过滤"(那会返回
    # 比用户预期更多的数据且毫无提示)。语义见 parse_as_of。
    as_of_ts = parse_as_of(as_of) if as_of is not None else None
    preset = _PURPOSE_PRESETS.get(purpose) if purpose is not None else None
    effective_kinds = (
        kinds
        if kinds is not None
        else (preset["kinds"] if preset else (REVISION, HYPOTHESIS))
    )
    effective_require_class = (
        require_class
        if require_class is not None
        else (preset["require_class"] if preset else None)
    )
    if anchor is None and question_id is not None:
        anchor = await _anchor_of(db, question_id)
    anchor = anchor or {}

    results: list[dict[str, Any]] = []
    for kind in effective_kinds:
        if kind not in _TABLE_FOR_KIND:
            raise ValueError(f"not recallable: {kind}")
        candidates = await _load_candidates(db, kind)
        # 账本回放:认识论过滤 —— as_of 之后才记录的不出现(前视排除)。
        # 置于 matched 门之前:AND 语义与位置无关,但早省掉后续查询。
        # "as_of 时刻已知"语义见 filter_known_by(解析不出→排除)。
        if as_of_ts is not None:
            candidates = filter_known_by(candidates, as_of_ts)
        for cand in candidates:
            matched: list[str] = []
            if question_id is not None and cand["question_id"] == question_id:
                matched.append("question:same")

            owner_ids = (
                [cand["question_id"]]
                if cand["question_id"]
                else await _question_ids_of(db, cand["id"])
            )
            for owner in owner_ids:
                if owner is None:
                    continue
                matched.extend(_anchor_overlap(anchor, await _anchor_of(db, owner)))
            matched = list(dict.fromkeys(matched))
            if not matched:
                continue

            chain = await _evidence_chain(db, kind, cand["id"])
            if subject is not None:
                stype, sid = subject
                for hop in chain:
                    hop_kind, hop_id = hop.split(":", 1)
                    if hop_kind != "episode":
                        continue
                    rows = await db.execute(
                        "SELECT 1 FROM research_episodes WHERE id = ? "
                        "AND subject_type = ? AND subject_id = ?",
                        (hop_id, stype, sid),
                    )
                    if rows:
                        matched.append(f"subject:{stype}")
                        break

            recall_class = LEARNED_EXPERIENCE if chain else RESEARCH_RECORD
            if (
                effective_require_class is not None
                and recall_class != effective_require_class
            ):
                continue

            superseded = (
                kind == HYPOTHESIS
                and cand.get("status") in _SUPERSEDED_HYPOTHESIS_STATUSES
            )
            results.append(
                {
                    "id": cand["id"],
                    "kind": kind,
                    "text": cand["text"],
                    "question_id": cand["question_id"],
                    "score": len(matched) - (_SUPERSEDED_PENALTY if superseded else 0),
                    "matched_on": matched,
                    "recall_class": recall_class,
                    "superseded": superseded,
                    "evidence_chain": chain,
                    "created_at": cand["created_at"],
                    # v025:仅 revision 候选带 horizon;hypothesis/evidence 无此键
                    **({"horizon": cand["horizon"]} if "horizon" in cand else {}),
                    # 回放模式:evidence 附数据窗标注(描述性,不参与打分)。
                    # 任一 boundary 缺失 → None(诚实说"不知道窗"),不伪造。
                    **(
                        {
                            "data_window": {
                                "start": cand.get("ep_boundary_start"),
                                "end": cand.get("ep_boundary_end"),
                            }
                        }
                        if as_of_ts is not None and kind == EVIDENCE
                        else {}
                    ),
                }
            )

    results.sort(key=lambda c: (-c["score"], c["created_at"], c["id"]), reverse=False)
    return results[:limit]

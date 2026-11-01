"""研究图谱仓储族的共享地基:id 生成、json 编解码、边类型注册表。

**边类型注册表是 §7 的唯一真源。** 任何地方要新增一种边,必须先在这里回答
一个问题:"它是事实,还是判断?" 答不出来 = 不许进册(§15.3 护栏)。
这条规则不是形式主义 —— 判断边必须携带推理依据与出处(D-3),事实边则
不允许携带,因为"不得把推断伪装成事实历史"。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _dump_json(value: Any) -> str:
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_json(raw: str | None) -> Any:
    if not raw:
        return None
    return json.loads(raw)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# --- §7 边类型注册表 -------------------------------------------------------

# **约定:列能承载的关系不写边。** 边是为"没有列可以表达的关系"发明的,
# 不是列的镜像 —— 同一关系存两份,一旦漂移就再也说不清哪个对。
#
#   走列(不写边,读时经 relations.list_relations 派生):
#     research_revisions.question_id  → revision_of
#     research_evidence.episode_id    → observed_from
#     (两列皆 NOT NULL 外键:行存在即关系存在,边是冗余的第二真相源)
#   写边(列表达不了):
#     derived_from  episode→episode   同表,无列
#     addresses     evidence→question 跨表,无列
#
# 消费者一律走 list_relations(边 ∪ 列派生),不要各读一半。
# 曾因此出过事故:/view 只读边表 → 30 条列关系对图谱不可见 → 前端自己合成。
#
# `revision_of` / `observed_from` 仍注册在册,因为派生行要用这两个名字;
# 但**不应有真边写入**(现库中为零,断言见 test_research_graph_relations)。

# 事实边:系统自动可加。描述"已经发生了什么",不带认识论判断。
FACT_EDGE_KINDS: frozenset[str] = frozenset(
    {
        "revision_of",  # revision → question
        "derived_from",  # question → question(分叉:原问题继续活着);
        # episode → episode(同 subject 重跑时间链:后续账本行从上一份派生,
        # 子→父方向,actor=system;跨 probe 类型的混合链是有意的同标的时间线)
        "superseded_by",  # question → question(原问题结束)
        "ran_in",  # experiment → mission / episode
        "observed_from",  # evidence → episode
        "addresses",  # mission / evidence → question(这个动作在回答哪个问题)
        "produced",  # experiment → evidence
        "led_to",  # decision → episode(Decision 之后发生了什么)
        "revokes",  # edge → edge(撤销是追加一条边,不是删边)
        "resolved_by",  # experiment(failed) → experiment(success)
        # 同一 hypothesis 下,最后一次失败被这次成功解决。
        # rd-agent 用 50 步无向图遍历表达这层关系(两处 bug 皆源于遍历),
        # 这里用显式事实边一步到位(internal-survey 7.2)。
    }
)

# 判断边:必须携带 rationale + provenance + actor(D-3)。
JUDGMENT_EDGE_KINDS: frozenset[str] = frozenset(
    {
        "supports",
        "contradicts",
        "qualifies",
        "applies_to",
    }
)

# 对称边只存一份(规范化顺序),否则 §4 的"引用迁移"统计会算重(§7)。
SYMMETRIC_EDGE_KINDS: frozenset[str] = frozenset({"relates_to"})

ALL_EDGE_KINDS: frozenset[str] = (
    FACT_EDGE_KINDS | JUDGMENT_EDGE_KINDS | SYMMETRIC_EDGE_KINDS
)


def canonical_pair(
    a: tuple[str, str], b: tuple[str, str]
) -> tuple[tuple[str, str], tuple[str, str]]:
    """对称边的规范顺序:小的一端永远在前,保证同一条边只有一种存法。"""
    return (a, b) if a <= b else (b, a)


# --- 窗口重看检测(F3:吸收 external-research-project 的"看过即降级"纪律)------------------
#
# external-research-project skills/external-research-project-research-architect/SKILL.md:131 的纪律:
# "在首次查看最终留出集前冻结公式、代码、配置和验收规则。任何实现……
#  一旦读取该区间的结果,该区间就不得再称为 untouched holdout;修复后应
#  换用新的未查看区间,或诚实降级为诊断结果。"
#
# Selene 侧的实测缺口(2026-10-03):同 (probe, symbol, interval) 的两次运行
# 窗口 99.8% 重叠(ep-ad71faf262d6 与 ep-d183ca183939 起点同为
# 2025-05-30T11:00,终点差 2 天),系统毫无提示 —— 后一次几乎完全重看了
# 前一次的窗口,却可能被当作独立样本外确认。
#
# **此函数只算重叠度,不做判断**:不阻断运行、不改阈值(与 F2 的
# subperiod_sign_consistency 同纪律 —— 诊断字段,非门禁)。


def _parse_iso(value: Any) -> datetime | None:
    """解析 ISO 时间串;解析不了返回 None(绝不抛)。

    为什么不用字符串比较:同仓 detect_stale_proof_points 曾拿自由文本的
    ``[:10]`` 当 ISO 日期做字典序比较,结果恒真、静默跳过、无任何报错
    (见 mission_thesis.py 的 deprecated 说明)。时间比较必须走 datetime。
    """
    if not value or not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def _is_past(iso_ts: Any, now: str | None = None) -> bool:
    """``iso_ts`` 是否早于 ``now``(缺省 = 当前 UTC)。

    本函数合并了此前散在 ``changes.py`` 与 ``objects.py`` 的两份 ``_is_past``
    —— 三处核心逻辑逐字相同(tzinfo 补 UTC + datetime 比较),只有两点差异,
    统一时**都保留**:
    - ``now`` 可注入(``changes`` 用它做确定性断言;``objects`` 用默认当前时刻)
    - ``_parse_iso`` 额外处理 ``"Z"`` 后缀(原两份不处理,是能力增强非行为变更)

    解析失败 → False(原两份的语义:"无从判断"不当作"已过期")。
    """
    moment = _parse_iso(iso_ts)
    if moment is None:
        return False
    ref = _parse_iso(now) if now is not None else datetime.now(UTC)
    if ref is None:
        return False
    return moment < ref


def window_replay_overlap(
    prior_windows: list[tuple[Any, Any]], start: Any, end: Any
) -> float | None:
    """本次窗口与历史窗口的最大重叠度(占本次窗口长度的比例)。

    ``prior_windows`` 是 ``[(boundary_start, boundary_end), ...]``。
    返回 ``max`` 重叠比例;**无重叠 / 首次运行 / 任一区间解析失败 → None**
    (None 表示"无从判断",不冒充 0.0 —— 0.0 会被读成"看过但没重叠")。

    区间退化(``start >= end``)同样返回 None:零长度窗口没有"看过多少"
    可言。
    """
    s = _parse_iso(start)
    e = _parse_iso(end)
    if s is None or e is None or s >= e:
        return None
    span = (e - s).total_seconds()
    best: float | None = None
    for prior in prior_windows or []:
        try:
            ps_raw, pe_raw = prior
        except (TypeError, ValueError):
            continue
        ps = _parse_iso(ps_raw)
        pe = _parse_iso(pe_raw)
        if ps is None or pe is None or ps >= pe:
            continue
        overlap = (min(e, pe) - max(s, ps)).total_seconds()
        if overlap <= 0:
            continue
        ratio = overlap / span
        if best is None or ratio > best:
            best = ratio
    return best

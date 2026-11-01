"""Deliverable classifier (C4) — observe-only quality gate on final answers.

学 vibe-trading incomplete≠failed 分类思想: 最终答案若只是"计划存根 /
编造数据 / 裸 tool-result 信封 / 未解析 tool 标记"就不算合格交付物。
本模块是**纯观察器**: 命中只 warn 不拦截(observe 先行, 收口判据见 plan
selene-0819-absorption-7items.md Story 6: 累计 ≥50 条 warn + 误判人工
抽检 0 例, 或两周到期, 二者取先)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class DeliverableClass(str, Enum):
    COMPLETED = "completed"
    PLAN_ONLY = "plan_only"
    FABRICATED_DATA = "fabricated_data"
    RAW_ENVELOPE = "raw_envelope"
    UNRESOLVED_TOKEN = "unresolved_token"


@dataclass(frozen=True)
class _Rule:
    cls: DeliverableClass
    pattern: re.Pattern[str]
    note: str


# 预编译 + 短路顺序: 最廉价/最特异在前
_RULES: tuple[_Rule, ...] = (
    # 未解析 tool 标记: provider 特殊 token 形状(含 U+FF5C 全角竖线)或裸标记
    _Rule(
        DeliverableClass.UNRESOLVED_TOKEN,
        re.compile(r"<\|[a-zA-Z_]+\|>|｜[a-zA-Z_]+｜"),
        "unresolved special-token shape leaked into final answer",
    ),
    # 裸 tool-result 信封: JSON 观察块直接当答案吐出
    _Rule(
        DeliverableClass.RAW_ENVELOPE,
        re.compile(
            r"^\s*\{?\s*\"(?:tool_result|observation|output)\"\s*:", re.IGNORECASE
        ),
        "raw tool-result envelope delivered as answer",
    ),
    # 编造关键词: 声称数据但通篇无来源
    _Rule(
        DeliverableClass.FABRICATED_DATA,
        re.compile(
            r"(?:数据|价格|指标).{0,12}(?:是我编的|为虚构|仅供参考虚构)|(?:I\s+made\s+up|fabricated)\s+(?:the\s+)?(?:data|numbers)",
            re.IGNORECASE,
        ),
        "answer admits fabricated data",
    ),
    # plan-only stub: 只有计划没有交付
    _Rule(
        DeliverableClass.PLAN_ONLY,
        re.compile(
            r"^(?:\s*(?:I\s+(?:would|will)\s+(?:start|begin|proceed|implement|perform)\b|我(?:将|会|打算)\s+(?:进行|开始|实现|做))|以下是我的?(?:计划|方案)[:：]?\s*$)",
            re.IGNORECASE,
        ),
        "answer is a plan stub, no deliverable",
    ),
)

_FABRIC_POSITIVE = re.compile(_RULES[2].pattern)

# plan opener 命中但有交付标记 → 不算 plan-only(只有计划无交付才算)
_PLAN_RULE = _RULES[3]
_DELIVERY_MARKER = re.compile(r"(?:Result|结果)\s*[:：]|[=\-−]\s*\d+(?:\.\d+)?")


def classify_deliverable(text: str) -> DeliverableClass:
    """纯函数: 最终答案 → DeliverableClass。零 IO, 无副作用。"""
    if not text or not text.strip():
        return DeliverableClass.COMPLETED
    for rule in _RULES[:3]:
        if rule.pattern.search(text):
            return rule.cls
    if _PLAN_RULE.pattern.search(text) and not _DELIVERY_MARKER.search(text):
        return DeliverableClass.PLAN_ONLY
    return DeliverableClass.COMPLETED

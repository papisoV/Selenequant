"""Question → Pillar 桥的草稿构造(纯函数,零 IO)。

背景(见 `internal-plan`):Selene 有两条
**完全隔离**的研究循环 —— question 链(council → revision,终点)与 mission 链
(mission → pillar → evidence,无人类创建面)。本模块是桥的"读侧":从一条
revision 里**提取已存在的内容**组装 pillar 草稿。

为什么是提取而不是发明:主持人五段契约(`investigate_council.py:378`:
【时间线】【观点整合与矛盾】【深层分析】【反向检验】【新问题】)与
``PillarCreate`` 同形 —— 两者都按"可证伪"设计。实测那条真 revision:

    【反向检验】会推翻当前判断的条件:…|d1|/|d0|≥0.5…  → break_threshold
    【新问题】1. …block bootstrap 分布…                → next_proof_point
    【时间线】…d0=-0.0058/-0.0059…                     → baseline

**本模块不做 LLM 调用、不写库、不判对错** —— 只把段落切出来填进草稿,
由用户在对话框里改完才提交(confirm-submit,见计划的 Phase 2/4)。
"""

from __future__ import annotations

import re
from typing import Any, Container, TypedDict


class RevisionSegments(TypedDict):
    """``parse_revision_segments`` 的返回形状 —— 三段映射值可为 None(段缺失),
    但 ``segments_found`` 恒为列表,故不能统一写成 ``dict[str, str | None]``。"""

    timeline: str | None
    reverse_check: str | None
    first_question: str | None
    segments_found: list[str]


# 五段契约的段名(与 investigate_council.py:378 的提示词逐字对应)。
# 段名改动 = 提示词契约变动 → 本模块的解析会失效,故测试用真文本锁住。
SECTION_TIMELINE = "时间线"
SECTION_CONTRADICTION = "观点整合与矛盾"
SECTION_ANALYSIS = "深层分析"
SECTION_REVERSE = "反向检验"
SECTION_QUESTIONS = "新问题"

# 参与 pillar 映射的三段;其余两段(观点整合/深层分析)留作对话展示,不映射。
_MAPPED_SECTIONS = (SECTION_TIMELINE, SECTION_REVERSE, SECTION_QUESTIONS)

_BASELINE_MAX = 300

# 编号行(与 investigate_council._NEW_QUESTION_LINE_RE 同款:1. / 1、 / 1) / - / *)
_NUMBERED_LINE_RE = re.compile(r"^\s*(?:\d+[.、)]|[-*])\s*(.+)$")

# claim 归一化时剥掉的尾部标点(含中英文问号/句号/叹号等)。
_TRAILING_PUNCT = "？?。.!！~～—-… \t\n"


def _section_body(content: str, name: str) -> str | None:
    """抽出 ``【name】`` 段的正文;段不存在或内容为"无" → None。

    段的边界 = 下一个 ``【…】`` 标题或文末。这与
    ``investigate_council._parse_new_questions`` 的取段方式一致。
    """
    pattern = rf"【{re.escape(name)}】\s*\n(.*?)(?=\n【|$)"
    m = re.search(pattern, content, re.DOTALL)
    if not m:
        return None
    body = m.group(1).strip()
    if not body or body == "无":
        return None
    return body


def _first_numbered_line(text: str | None) -> str | None:
    """取编号列表的第一条(去掉编号)。没有编号行 → None。"""
    if not text:
        return None
    for raw in text.splitlines():
        m = _NUMBERED_LINE_RE.match(raw.strip())
        if m:
            title = m.group(1).strip()
            if title and title != "无":
                return title
    return None


def parse_revision_segments(content: str) -> RevisionSegments:
    """把 revision 正文按五段契约切开,返回可映射的三段。

    返回 ``{timeline, reverse_check, first_question, segments_found}``:
    - ``timeline`` → pillar.baseline(截 ``_BASELINE_MAX`` 字符)
    - ``reverse_check`` → pillar.break_threshold(整段,不截 —— 它是判据)
    - ``first_question`` → pillar.next_proof_point(【新问题】的第一条编号行)
    - ``segments_found``:实际识别到的段名列表,供 UI 明示"识别到 N/3 段"

    **永不抛异常**:手写 revision(走"自己写终版"路径)没有五段结构,
    此时三个值全 None、``segments_found`` 为空 —— 这是合法输入,不是错误。
    """
    if not content or not isinstance(content, str):
        return {
            "timeline": None,
            "reverse_check": None,
            "first_question": None,
            "segments_found": [],
        }

    found: list[str] = []
    timeline = _section_body(content, SECTION_TIMELINE)
    contradiction = _section_body(content, SECTION_CONTRADICTION)
    analysis = _section_body(content, SECTION_ANALYSIS)
    reverse = _section_body(content, SECTION_REVERSE)
    questions = _section_body(content, SECTION_QUESTIONS)

    for name, body in (
        (SECTION_TIMELINE, timeline),
        (SECTION_CONTRADICTION, contradiction),
        (SECTION_ANALYSIS, analysis),
        (SECTION_REVERSE, reverse),
        (SECTION_QUESTIONS, questions),
    ):
        if body is not None:
            found.append(name)

    if timeline is not None and len(timeline) > _BASELINE_MAX:
        timeline = timeline[:_BASELINE_MAX] + "…(截断)"

    return {
        "timeline": timeline,
        "reverse_check": reverse,
        "first_question": _first_numbered_line(questions),
        "segments_found": found,
    }


def normalize_claim(text: str) -> str:
    """剥离首尾空白与尾部标点 —— 用于 claim 硬门比对(见计划 Phase 2)。

    ``norm(claim) == norm(question.title)`` → 提交被拒(422):claim 必须是
    可证伪陈述,不能直接是问题标题。
    """
    return (text or "").strip().rstrip(_TRAILING_PUNCT)


def strip_question_mark(title: str) -> str:
    """问题标题 → claim 预填值(去尾部问号)。

    注意:这只是**脚手架**,不是合法 claim —— 剥掉问号的问题仍是问句
    (「…half_delay 在哪一档」),不是可证伪陈述。故 ``build_promote_draft``
    的 warnings 恒含一条提醒用户改写,且 Phase 2 有硬门兜底。
    """
    return (title or "").strip().rstrip("？? \t\n")


def suggest_subject(
    anchor: dict[str, Any] | None,
    known_strategy_ids: Container[str] | None = None,
) -> tuple[tuple[str, str] | None, str]:
    """anchor → (subject_type, subject_id) 的**建议式**映射。

    规则(计划裁决 c;``intervals`` 永不参与 —— 时间框架不是主体):
    1. ``strategy_ids`` 非空 → ``("strategy", 第一个)``
    2. 否则含 ``symbols``/``symbol`` → ``("market", 该值)``
    3. 否则 → ``(None, 理由)`` —— **永不静默默认**,交还用户手选

    ``known_strategy_ids`` 传入"已注册策略 id 集"时启用**诚实模式**
    (2026-10-13):``strategy_ids[0]`` 若不在集内,说明 anchor 挂的其实是探针名
    (``entry-delay-ic-probe``)/回测 id(``bt-...``),不是策略 —— 此时降级为
    ``(None, 理由)`` 交还用户手选,而不是把脏 id 预填进对话框。
    ``None`` = 调用方无策略登记表可查,退回纯建议行为(不谎称已校验;真正的
    落库安全由 :meth:`ResearchMissionRepository.create` 的硬门兜底)。

    返回 ``(suggestion_or_None, reason)``;reason 用于 UI 展示与审计。
    """
    anchor = anchor or {}
    sids = anchor.get("strategy_ids")
    if isinstance(sids, (list, tuple)) and sids:
        chosen = str(sids[0])
        reason = "取自 anchor.strategy_ids[0]"
        if len(sids) > 1:
            reason += f"(共 {len(sids)} 个,取第一个)"
        if known_strategy_ids is not None and chosen not in known_strategy_ids:
            return (
                None,
                f"anchor.strategy_ids[0]={chosen} 不是已注册策略"
                "(像是探针/回测 id)—— 请手选真实策略主体",
            )
        return ("strategy", chosen), reason

    for key in ("symbols", "symbol"):
        val = anchor.get(key)
        if isinstance(val, (list, tuple)) and val:
            return ("market", str(val[0])), f"取自 anchor.{key}[0]"
        if isinstance(val, str) and val.strip():
            return ("market", val.strip()), f"取自 anchor.{key}"

    keys = sorted(anchor.keys()) if anchor else []
    return (
        None,
        "anchor 无可映射键(实有:" + (", ".join(keys) if keys else "空") + ")",
    )


def build_promote_draft(
    question: dict[str, Any],
    revision: dict[str, Any],
    known_strategy_ids: Container[str] | None = None,
) -> dict[str, Any]:
    """组装 promote 草稿(mission + pillar + subject 建议 + warnings)。

    **预填 ≠ 落库**:调用方把本函数结果渲染进对话框,用户在 UI 里改完
    再走 ``POST /questions/{id}/promote``。取消 = 零写入。

    ``known_strategy_ids`` 透传给 :func:`suggest_subject` —— 传入已注册策略
    id 集时,挂错主体(探针名/回测 id)的 anchor 不再被预填成 strategy,
    而是降级为"主体未映射"警告交还用户手选。
    """
    title = str(question.get("title") or "")
    seq = revision.get("seq")
    rev_id = str(revision.get("id") or "")
    segments = parse_revision_segments(str(revision.get("content") or ""))
    subject, subject_reason = suggest_subject(
        question.get("anchor"), known_strategy_ids
    )

    warnings: list[str] = []
    # 恒含一条:五段契约无【结论】段 → claim 预填必然是问句剥离版。
    warnings.append(
        "claim 目前是问题标题的问号剥离版,不是可证伪陈述 — "
        "提交会被拒绝;请改写为 revision 结论句"
    )
    if subject is None:
        warnings.append(f"主体未映射:{subject_reason} — 请手动选择")
    if not segments["segments_found"]:
        warnings.append("未识别到五段结构(该 revision 可能是手写的)— 字段需全手填")

    next_proof = segments["first_question"]
    return {
        "mission": {
            "title": title,
            "question": str(question.get("body") or "") or title,
            "subject_type": subject[0] if subject else None,
            "subject_id": subject[1] if subject else None,
        },
        "pillar": {
            "claim": strip_question_mark(title),
            "priority": "core",
            "baseline": segments["timeline"],
            "break_threshold": segments["reverse_check"],
            "next_proof_point": next_proof,
            "next_proof_source": f"{rev_id} 新问题1" if next_proof else None,
            "model_line": f"council v{seq} {rev_id}",
            "provenance": "draft",
            "threshold_provenance": "draft",
        },
        "subject_suggestion": (
            {"subject_type": subject[0], "subject_id": subject[1]} if subject else None
        ),
        "subject_reason": subject_reason,
        "segments_found": segments["segments_found"],
        "revision_id": rev_id,
        "revision_seq": seq,
        "warnings": warnings,
    }

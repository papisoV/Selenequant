"""Question → Pillar 桥的草稿构造 —— 纯函数,不需要 DB。

锁 ``_promote_draft`` 的提取行为。核心前提:council 五段契约
(investigate_council.py:378)与 PillarCreate 同形,所以桥是**提取已存在
的内容**,不是发明。本文件用**真 revision 文本**做 fixture
(rev-75648aefe761, 1661 字符,来自真库)—— 若提示词改动导致五段结构漂移,
这些测试立刻变红。

纪律:解析永不抛异常(手写 revision 无五段结构是**合法输入**)。
"""

from __future__ import annotations

from selene_space_storage.repositories.research_graph._promote_draft import (
    build_promote_draft,
    normalize_claim,
    parse_revision_segments,
    strip_question_mark,
    suggest_subject,
)

# 真 revision 文本(rev-75648aefe761)。刻意保留原样(含全角标点与反引号)——
# 这是 LLM 真实产出,解析器必须对它有反应。
REAL_REVISION = """【时间线】
B4 第三刀固定 BTC/USDT 1h、持有窗 5 bar，对 `funding_z` 做整体后移，delay 网格为 {0,1,3,5,10,15,30,60}，`ic_half_delay_funding_z` 定义为网格中首个剩余 |rank-IC| ≤ 0.5×|IC(delay=0)|，无插值。两条已锚定记录 ev-1d2d81345ef3、ev-df4b441e8234 相隔约两天，n≈11.5k，数值接近：d0=-0.0058/-0.0059，d1=-0.0026/-0.0028，d3=+0.0037/+0.0033，d5=+0.0118/+0.0112，d10=+0.0133/+0.0129，d15=-0.0101/-0.0102，d30=+0.0006/+0.0006，d60=+0.0173/+0.0176。由此半阈约 0.0029/0.00295，首个≤半值档为 delay=1。

【观点整合与矛盾】
机械定义下，`half_delay=1`。经验派与挑战派在这一点一致；记录派"未定档、先验偏 5"的判断有误。

【深层分析】
该曲线不是单调半衰，而是振荡和符号翻转：d0/d1 为负，d3 转正。

【反向检验】
会推翻当前判断的条件：新 episode 或滚动重跑后 |d1|/|d0|≥0.5，或 block bootstrap/Newey-West 置信区间跨 0.5；则 `half_delay≠1`，并应检查是否跳到 30。观察者应盯：完整 IC 表及符号、d0/d1 比例。

【新问题】
1. 在"无插值、首个≤半值"定义下，d1 与 0.5×|IC0| 的差异是否统计显著？`half_delay` 的 block bootstrap 分布和 90% 集合是什么？
2. 若把定义改为"持续≤半值且符号不反转"，答案是 d30、不存在，还是其他档？
3. 固定持有窗整体后移造成的档位，有多少来自 `funding_z` 真正迟到衰减？
"""

TITLE = "BTC 1h funding 信号进场延迟衰减:half_delay 在哪一档?"


# --- parse_revision_segments:真文本 -----------------------------------------


def test_real_revision_all_five_sections_found() -> None:
    seg = parse_revision_segments(REAL_REVISION)
    assert seg["segments_found"] == [
        "时间线",
        "观点整合与矛盾",
        "深层分析",
        "反向检验",
        "新问题",
    ]


def test_real_revision_maps_to_pillar_fields() -> None:
    """三段映射到 pillar 的三字段 —— 这是"提取而非发明"的核心断言。"""
    seg = parse_revision_segments(REAL_REVISION)

    # baseline ← 【时间线】,含锚定数值
    assert seg["timeline"] is not None
    assert "-0.0058" in seg["timeline"]

    # break_threshold ← 【反向检验】,含真正的翻案判据
    assert seg["reverse_check"] is not None
    assert "0.5" in seg["reverse_check"]
    assert "置信区间" in seg["reverse_check"]

    # next_proof_point ← 【新问题】第一条(编号已剥掉)
    assert seg["first_question"] is not None
    assert seg["first_question"].startswith("在")
    assert "block bootstrap" in seg["first_question"]
    assert not seg["first_question"].startswith("1.")
    # 只取第一条,不含第二条
    assert "d30" not in seg["first_question"]


def test_timeline_truncated_to_300() -> None:
    """【时间线】截断到 300 字符并标注。"""
    seg = parse_revision_segments(REAL_REVISION)
    assert seg["timeline"] is not None
    assert len(seg["timeline"]) <= 300 + len("…(截断)")
    assert seg["timeline"].endswith("…(截断)")


# --- parse_revision_segments:反例(永不抛) ----------------------------------


def test_handwritten_revision_yields_no_segments() -> None:
    """手写 revision(走"自己写终版"路径)无五段 —— 合法输入,不抛。"""
    seg = parse_revision_segments("我自己写的一段终版结论,没有分节结构。")
    assert seg["segments_found"] == []
    assert seg["timeline"] is None
    assert seg["reverse_check"] is None
    assert seg["first_question"] is None


def test_empty_or_none_content_is_safe() -> None:
    bad_inputs: tuple[object, ...] = ("", None, 123, {})
    for bad in bad_inputs:
        seg = parse_revision_segments(bad)  # type: ignore[arg-type]
        assert seg["segments_found"] == []
        assert seg["first_question"] is None


def test_section_with_only_wu_is_treated_as_absent() -> None:
    """段落内容为"无" → 视为该段缺失(主持人有时写"无")。"""
    seg = parse_revision_segments("【时间线】\n无\n\n【反向检验】\n有内容")
    assert seg["timeline"] is None
    assert seg["reverse_check"] == "有内容"


def test_questions_section_without_numbered_lines() -> None:
    """【新问题】段存在但无编号行 → first_question 为 None(不硬失败)。"""
    seg = parse_revision_segments("【新问题】\n随便写点没有编号的东西")
    assert seg["first_question"] is None
    assert "新问题" in seg["segments_found"]


# --- normalize_claim / strip_question_mark:claim 硬门 ------------------------


def test_claim_gate_detects_unmodified_title() -> None:
    """硬门:claim == 问号剥离后的 title → 归一化后相等(调用方据此 422)。"""
    assert normalize_claim(TITLE) == normalize_claim(strip_question_mark(TITLE))


def test_claim_gate_passes_on_real_statement() -> None:
    """真正改写成陈述句 → 不等,过门。"""
    claim = "BTC 1h 上 half_delay=1,但只是条件性机械点估计,不是稳健交易结论。"
    assert normalize_claim(claim) != normalize_claim(TITLE)


def test_normalize_strips_trailing_punct_variants() -> None:
    """各类尾部标点都剥掉(半角/全角问号、句号、叹号、波浪号)。"""
    base = "同一个陈述"
    for punct in ("？", "?", "。", ".", "！", "!", "~", "～", "…", " ", "\t"):
        assert normalize_claim(base + punct) == base


def test_normalize_preserves_inner_punctuation() -> None:
    """只剥尾部;中间标点保留(否则会误判不同的 claim 相等)。"""
    assert normalize_claim("A,B。C") == "A,B。C"


# --- suggest_subject:建议式映射 ----------------------------------------------


def test_rule1_strategy_ids_wins() -> None:
    subj, reason = suggest_subject(
        {"intervals": ["1h"], "strategy_ids": ["entry-delay-ic-probe"]}
    )
    assert subj == ("strategy", "entry-delay-ic-probe")
    assert "strategy_ids[0]" in reason


def test_rule1_multiple_ids_takes_first_and_says_so() -> None:
    subj, reason = suggest_subject({"strategy_ids": ["a", "b", "c"]})
    assert subj == ("strategy", "a")
    assert "共 3 个" in reason


def test_rule1_downgrades_when_id_not_registered() -> None:
    """诚实模式(2026-10-13):strategy_ids[0] 不在已注册集 → 不预填脏主体。

    anchor 挂的常是探针名(entry-delay-ic-probe)/回测 id,不是策略 —— 传
    known_strategy_ids 时必须降级成 (None, 理由),交还用户手选。
    """
    subj, reason = suggest_subject(
        {"strategy_ids": ["entry-delay-ic-probe"]},
        known_strategy_ids={"strat-6395c312"},
    )
    assert subj is None
    assert "不是已注册策略" in reason
    # 集内 id 照常映射
    ok, _ = suggest_subject(
        {"strategy_ids": ["strat-6395c312"]}, known_strategy_ids={"strat-6395c312"}
    )
    assert ok == ("strategy", "strat-6395c312")


def test_rule1_without_registry_keeps_pure_suggestion() -> None:
    """known_strategy_ids=None = 无表可查 → 退回纯建议(不谎称已校验)。"""
    subj, reason = suggest_subject({"strategy_ids": ["entry-delay-ic-probe"]})
    assert subj == ("strategy", "entry-delay-ic-probe")
    assert "不是已注册策略" not in reason


def test_rule2_symbols() -> None:
    assert suggest_subject({"symbols": ["BTC/USDT"]})[0] == ("market", "BTC/USDT")
    assert suggest_subject({"symbol": "ETH/USDT"})[0] == ("market", "ETH/USDT")


def test_intervals_never_maps_subject() -> None:
    """时间框架不是主体 —— 只有 intervals 时必须交还用户。"""
    subj, reason = suggest_subject({"intervals": ["1h"]})
    assert subj is None
    assert "intervals" in reason


def test_empty_anchor_returns_none_never_silent_default() -> None:
    """空 anchor → None(永不静默默认)。"""
    assert suggest_subject({})[0] is None
    assert suggest_subject(None)[0] is None


# --- build_promote_draft:端到端组装 -----------------------------------------


def test_build_draft_maps_real_inputs() -> None:
    question = {
        "title": TITLE,
        "body": "四段研究背景…",
        "anchor": {"intervals": ["1h"], "strategy_ids": ["entry-delay-ic-probe"]},
    }
    revision = {"id": "rev-75648aefe761", "seq": 1, "content": REAL_REVISION}
    draft = build_promote_draft(question, revision)

    assert draft["mission"]["subject_type"] == "strategy"
    assert draft["mission"]["subject_id"] == "entry-delay-ic-probe"
    assert draft["pillar"]["break_threshold"] is not None
    assert draft["pillar"]["next_proof_point"] is not None
    assert draft["pillar"]["model_line"] == "council v1 rev-75648aefe761"
    assert draft["pillar"]["provenance"] == "draft"
    assert draft["pillar"]["threshold_provenance"] == "draft"
    assert draft["revision_seq"] == 1


def test_draft_warnings_always_include_claim_scaffold_note() -> None:
    """warnings 恒含 claim 预标条(五段无【结论】段 → 预填必是问句)。"""
    question = {"title": TITLE, "anchor": {"strategy_ids": ["s1"]}}
    revision = {"id": "r1", "seq": 2, "content": REAL_REVISION}
    draft = build_promote_draft(question, revision)
    assert any("claim" in w and "改写" in w for w in draft["warnings"])


def test_draft_warns_when_subject_unmapped() -> None:
    question = {"title": "只有周期的问题?", "anchor": {"intervals": ["4h"]}}
    revision = {"id": "r1", "seq": 1, "content": REAL_REVISION}
    draft = build_promote_draft(question, revision)
    assert draft["subject_suggestion"] is None
    assert any("主体未映射" in w for w in draft["warnings"])


def test_draft_known_strategies_downgrades_bad_anchor() -> None:
    """known_strategy_ids 传入 → 挂错主体的 anchor 降级为"主体未映射"警告。"""
    question = {
        "title": TITLE,
        "anchor": {"strategy_ids": ["entry-delay-ic-probe"]},
    }
    revision = {"id": "r1", "seq": 1, "content": REAL_REVISION}
    draft = build_promote_draft(
        question, revision, known_strategy_ids={"strat-6395c312"}
    )
    assert draft["subject_suggestion"] is None
    assert draft["mission"]["subject_type"] is None
    assert any("主体未映射" in w for w in draft["warnings"])
    assert "不是已注册策略" in draft["subject_reason"]


def test_draft_warns_when_no_five_section_structure() -> None:
    question = {"title": "手写问题?", "anchor": {"strategy_ids": ["s1"]}}
    revision = {"id": "r1", "seq": 1, "content": "纯手写,无分节"}
    draft = build_promote_draft(question, revision)
    assert draft["segments_found"] == []
    assert any("五段结构" in w for w in draft["warnings"])
    # claim 兜底仍在(有可编辑起点,不是空表单)
    assert draft["pillar"]["claim"]


def test_draft_next_proof_source_absent_when_no_question_found() -> None:
    """无【新问题】编号行 → next_proof_point/source 均为 None(不编造)。"""
    question = {"title": "x?", "anchor": {"strategy_ids": ["s1"]}}
    revision = {"id": "r1", "seq": 1, "content": "【时间线】\n有"}
    draft = build_promote_draft(question, revision)
    assert draft["pillar"]["next_proof_point"] is None
    assert draft["pillar"]["next_proof_source"] is None

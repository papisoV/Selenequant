# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""AI 大盘复盘 prompt construction — system prompt, data slices, summary line.

## The redline is the product

The recap's whole value is that it is an **objective** market description, not
advice. TSP encodes that as a block of forbidden phrasings in the system prompt,
and a report that slips one of them in is a defect, not a style nit. The
forbidden-phrase list is a module constant (:data:`FORBIDDEN_PHRASES`) rather
than prose buried in the prompt, so a test can assert both that the prompt
contains it and that every phrase in it is named in the prompt.

## Divergences from TSP

* **Percentages are percent numbers everywhere.** TSP's prompt builders receive
  a mix: ``breadth.avg_pct`` and board ``avg_pct`` are fractions while
  ``up_pct`` is a percentage. Here everything is a percent number, so
  :func:`fmt_pct` formats directly with no ``* 100``.
* **Amount may be unavailable.** TSP always has turnover; selene's daily base
  does not. When ``amount.available`` is false the line says so instead of
  printing ``0 亿元``, which would read as a dead market.
* **No 盘前风向标 block.** TSP's comes from a paid fuyao feed that this
  deployment cannot reach; the section is simply absent rather than empty.
* **龙虎榜 is optional and free.** selene reads it from the Eastmoney
  datacenter, so the block appears whenever the day has rows.

## Output protocol

:func:`build_user_prompt` returns the user message; the streaming layer wraps
``SYSTEM_PROMPT`` + that message into ``LLMMessage`` objects (a raw dict raises
on ``.role`` in the provider's payload builder).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

# The four core indices, short form. Same table as `index_quotes.INDEX_SHORT`
# but keyed by name because the recap summary reads a quote's display name
# (Sina's own field) rather than a symbol.
INDEX_SHORT: dict[str, str] = {
    "上证指数": "上",
    "深证成指": "深",
    "创业板指": "创",
    "科创综指": "科",
    "科创50": "科",
}

# Phrases that would turn the report into an instruction. Named separately so a
# test can check the prompt still forbids each one — a prompt edit that drops a
# term silently weakens the redline.
FORBIDDEN_PHRASES: tuple[str, ...] = (
    "进攻",
    "防守",
    "加仓",
    "减仓",
    "轻仓",
    "半仓",
    "重仓",
    "仓位建议",
    "低吸",
    "反包",
    "追高",
    "回避方向",
)

# The disclaimer that must close every report. The prompt asks for it and the
# service appends it if the model omits it (see `ensure_disclaimer`).
DISCLAIMER = (
    "> ⚠️ 本内容由 AI 基于公开行情数据生成,仅客观陈述市场状态,"
    "不构成任何投资建议或买卖指令。交易有风险,入市需谨慎。"
)

SYSTEM_PROMPT = f"""你是一位拥有 15 年 A 股一线研究经验的市场分析师,擅长从指数结构、涨跌家数、连板梯队、板块轮动与资金情绪中客观提炼市场主线,产出一份**客观、中立、不包含任何买卖或操作建议**的盘后复盘报告。

## 核心红线(务必遵守)

- **绝对不输出**"{"/".join(FORBIDDEN_PHRASES[:9])}/{"/".join(FORBIDDEN_PHRASES[9:])}"等任何交易指令或倾向性措辞
- 你的角色是**客观陈述**今日市场的结构、情绪、板块轮动特征,以及后续值得客观关注的盘面信号
- 换成"一个中立财经记者能不能写出来"——能写就保留,不能写就删除

## 输出规范

用 **Markdown** 格式输出,严格遵循以下结构。不要输出任何 JSON 或代码块,直接输出 Markdown 正文。

### 1. 🎯 一句话定调(1-2 句)
用一句话概括今日市场的**核心矛盾与状态**(如"放量普涨、情绪修复,主线围绕科技扩散"/"指数走强、个股普跌,赚钱效应偏弱")。结尾用【市场状态:偏强 / 中性 / 偏弱】客观描述当日市场强弱,**不下基调结论、不指挥操作**。

### 2. 📊 盘面总览
- 三大指数(上证/深证/创业板)表现:谁强谁弱、量能配合
- 涨跌家数、涨停/跌停/炸板结构、两市成交额(放量/缩量判断)
- 情绪温度(强势/偏暖/震荡/偏冷/冰点)及一句话依据

### 3. 📈 指数结构
谁在走强、谁在走弱;指数是否同步;关键支撑/压力位(基于当日点位推断);是否存在量价背离。

### 4. 🔥 板块主线
- 领涨板块:背后的逻辑(消息/业绩/资金/技术)、持续性判断
- 领跌板块:客观风险信号、是否扩散
- 连板梯队与投机情绪:最高连板、封板率、炸板率反映的资金活跃程度

### 5. 💰 资金与情绪
成交额结构(增量/存量)、市场宽度(上涨占比、站上均线占比)、量能指标(量比)解读;风险偏好是修复还是转弱。

### 6. 📰 消息催化
结合提供的近期新闻,客观提炼可能影响后续盘面的催化或扰动,明确区分"已兑现"与"待发酵"。**若无新闻数据,则直接从量价异动客观推断可能的催化逻辑并给出结论,不要标注"[推断]"之类的过程标签,更不要编造具体消息。**

### 7. 📌 后续观察要点
- 客观列出明日值得关注的盘面信号(如量能能否维持、某均线得失、某板块持续性)
- 客观描述不同情景下市场结构的可能演变(如"若量能持续放大,普涨格局或延续";"若量能萎缩,结构性行情为主"),**不涉及仓位与买卖方向**
- **不输出**"仓位建议""进攻/防守基调""买卖方向""追高/低吸/反包"等操作指令

### 8. ⚠️ 风险提示
列出需要客观关注的风险点(如量能跟不上、外资流出、连板断层等)。末尾附一行:
"{DISCLAIMER}"

## 分析准则(务必遵守)

0. **只输出结论,不输出思考过程**:禁止复述你的分析步骤或方法论。不要写"我先按...做结构化复盘""接下来看...""基于上述数据我认为"这类元话语——直接给结论。读者要的是复盘结果,不是你怎么推导出来的。
1. **数据说话**:每个判断引用具体数值,严禁空泛套话("情绪回暖"必须改成"涨停 68 家较前日 +22,封板率 75%")
2. **客观中立**:看多就客观陈述多头特征,看空就客观陈述空头特征,不下基调、不骑墙;数据不支持时直言无法判断
3. **结构优先**:先看指数同步性与量能结构,再看板块与情绪,最后才是消息
4. **不重复数字**:正文负责解读表格数据背后的含义,不要照抄罗列已提供的大段原始数字
5. **不输出操作指令**:不写"仓位建议""进攻/防守""买卖方向"等任何交易指令
6. **简明客观**:用读者能扫读的密度输出,总字数 1200-2000 字,重在客观信息密度

现在请基于下方数据进行复盘。"""


def fmt_pct(value: Any) -> str:
    """Percent number → ``+1.23%`` / ``-0.45%`` / ``—``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:  # NaN
        return "—"
    return f"{number:+.2f}%"


def fmt_yi(value: Any) -> str:
    """Yuan amount → ``1234 亿`` / ``—``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number / 1e8:.0f} 亿"


def _indices_block(overview: Mapping[str, Any]) -> str:
    indices: Sequence[Mapping[str, Any]] = overview.get("indices") or []
    if not indices:
        reason = overview.get("indices_reason")
        return f"(指数行情不可用{': ' + str(reason) if reason else ''})"
    lines = []
    for idx in indices:
        name = idx.get("name") or idx.get("symbol") or "?"
        price = idx.get("last_price")
        price_s = f"{float(price):.2f}" if price is not None else "—"
        lines.append(f"- {name}: {price_s}  {fmt_pct(idx.get('change_pct'))}")
    return "\n".join(lines)


def _breadth_block(overview: Mapping[str, Any]) -> str:
    breadth = overview.get("breadth") or {}
    amount = overview.get("amount") or {}
    limit = overview.get("limit") or {}
    trend = overview.get("trend") or {}
    activity = overview.get("activity") or {}
    coverage = overview.get("coverage") or {}

    lines = [
        f"- 上涨/下跌/平盘: {breadth.get('up', 0)} / {breadth.get('down', 0)} / "
        f"{breadth.get('flat', 0)}  (上涨占比 {float(breadth.get('up_pct') or 0):.1f}%)",
        f"- 涨停/炸板/跌停: {limit.get('limit_up', 0)} / {limit.get('broken', 0)} / "
        f"{limit.get('limit_down', 0)}  (封板率 {float(limit.get('seal_rate') or 0):.0f}%, "
        f"最高连板 {limit.get('max_boards', 0)})",
    ]
    if limit.get("tiers"):
        tiers = "、".join(
            f"{t['boards']}板×{t['count']}" for t in list(limit["tiers"])[:5]
        )
        lines.append(f"- 连板梯队: {tiers}")
    if amount.get("available"):
        lines.append(f"- 两市成交额: {fmt_yi(amount.get('total'))}")
    else:
        lines.append("- 两市成交额: (本次数据源不含成交额,不做放量/缩量判断)")
    lines.append(
        f"- 均线站位: MA5 {float(trend.get('above_ma5_pct') or 0):.0f}% / "
        f"MA20 {float(trend.get('above_ma20_pct') or 0):.0f}% / "
        f"MA60 {float(trend.get('above_ma60_pct') or 0):.0f}%"
    )
    lines.append(
        f"- 量能: 量比5日均 {float(activity.get('avg_vol_ratio') or 1):.2f}, "
        f"放量股(量比≥1.5)占比 {float(activity.get('high_vol_pct') or 0):.1f}%"
    )
    if coverage.get("ratio") is not None:
        lines.append(
            f"- 覆盖度: 行情 {coverage.get('quoted')}/{coverage.get('universe')} 只"
            f"({float(coverage.get('ratio') or 0) * 100:.1f}%)"
        )
    return "\n".join(lines)


def _sector_block(rank: Mapping[str, Any] | None, label: str) -> str:
    if not rank or not (rank.get("leading") or rank.get("lagging")):
        return "- (暂无数据)"

    def _fmt(items: Sequence[Mapping[str, Any]] | None) -> str:
        if not items:
            return "—"
        return "、".join(
            f"{it.get('name')}({fmt_pct(it.get('avg_pct'))},"
            f"领涨:{((it.get('leader') or {}).get('name')) or '—'})"
            for it in list(items)[:5]
        )

    return f"- 领涨{label}: {_fmt(rank.get('leading'))}\n- 领跌{label}: {_fmt(rank.get('lagging'))}"


def _emotion_block(overview: Mapping[str, Any]) -> str:
    emotion = overview.get("emotion") or {}
    radar = overview.get("radar") or []
    score = emotion.get("score")
    lines = [
        f"- 情绪温度: {score} ({emotion.get('label') or '—'})"
        if score is not None
        else "- 情绪温度: 不可用(无行情截面)"
    ]
    if radar:
        dims = "、".join(f"{r.get('label')}{r.get('value', 0)}" for r in radar)
        lines.append(f"- 六维雷达: {dims}")
    return "\n".join(lines)


def _lhb_block(lhb_context: str) -> str:
    if not lhb_context:
        return ""
    return f"\n## 龙虎榜资金动向\n{lhb_context}\n"


def _news_block(news: Sequence[Mapping[str, Any]] | None) -> str:
    if not news:
        return (
            "\n## 近期市场新闻\n"
            "(暂无新闻数据:消息催化一节请直接从量价异动给出可能的催化逻辑结论,"
            "不要编造具体消息,也不要复述本说明。)\n"
        )
    lines = []
    for i, item in enumerate(list(news)[:8], 1):
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        meta = " / ".join(
            str(p).strip()
            for p in (item.get("source"), item.get("published_date"))
            if p
        )
        head = f"{i}. {title} ({meta})" if meta else f"{i}. {title}"
        lines.append(f"{head}\n   {snippet}" if snippet else head)
    return "\n## 近期市场新闻\n" + "\n".join(lines) + "\n"


def build_user_prompt(
    overview: Mapping[str, Any],
    *,
    news: Sequence[Mapping[str, Any]] | None = None,
    focus: str = "",
    lhb_context: str = "",
) -> str:
    """Assemble the user message from an overview payload.

    ``focus`` is the caller's extra instruction (e.g. "重点看半导体"); it is
    appended verbatim under a heading that restates the redline, so a focus
    request cannot be used to smuggle an advisory angle into the report.
    """
    as_of = overview.get("as_of") or "今日"
    parts = [
        f"复盘日期: {as_of}",
        "",
        "## 主要指数",
        _indices_block(overview),
        "",
        "## 盘面数据",
        _breadth_block(overview),
        "",
        "## 市场情绪",
        _emotion_block(overview),
        "",
        "## 概念板块排名",
        _sector_block(overview.get("concept_rank"), "概念"),
        "",
        "## 行业板块排名",
        _sector_block(overview.get("industry_rank"), "行业"),
        _lhb_block(lhb_context),
        _news_block(news),
    ]
    focus_text = str(focus or "").strip()
    if focus_text:
        parts.extend(
            [
                "",
                "## 用户关注点",
                focus_text,
                "(以上关注点只影响分析侧重;红线不变——仍不得输出任何交易指令"
                "或倾向性措辞。)",
            ]
        )
    return "\n".join(parts)


def recap_summary(overview: Mapping[str, Any]) -> str:
    """One-line summary for the ``meta`` event and history lists."""
    indices = overview.get("indices") or []
    emotion = overview.get("emotion") or {}
    limit = overview.get("limit") or {}
    amount = overview.get("amount") or {}

    idx_str = (
        "、".join(
            f"{INDEX_SHORT.get(str(i.get('name') or ''), str(i.get('name') or ''))}"
            f"{fmt_pct(i.get('change_pct'))}"
            for i in indices[:4]
        )
        or "指数缺失"
    )
    amount_str = (
        f"成交{fmt_yi(amount.get('total'))}"
        if amount.get("available")
        else "成交额不可用"
    )
    score = emotion.get("score")
    emotion_str = (
        f"情绪{score}({emotion.get('label') or '—'})"
        if score is not None
        else "情绪不可用"
    )
    return f"{idx_str} | {emotion_str} | 涨停{limit.get('limit_up', 0)} | {amount_str}"


def ensure_disclaimer(text: str) -> str:
    """Append the disclaimer when the model omitted it.

    The prompt asks for it, but a model that drops the last section would ship
    an AI market report with no disclaimer at all — the one failure the redline
    cannot tolerate. Idempotent: text already carrying it is returned unchanged.
    """
    if "不构成任何投资建议" in text:
        return text
    stripped = text.rstrip()
    return f"{stripped}\n\n{DISCLAIMER}\n" if stripped else DISCLAIMER


__all__ = [
    "DISCLAIMER",
    "FORBIDDEN_PHRASES",
    "INDEX_SHORT",
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "ensure_disclaimer",
    "fmt_pct",
    "fmt_yi",
    "recap_summary",
]

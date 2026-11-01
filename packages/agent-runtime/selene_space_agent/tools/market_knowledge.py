"""Market knowledge base — distilled trading cognition for the guide tool.

Data layer only: no behavior, no I/O.  Entries are matched by
(regime × intent) plus optional conditional matchers.  Export names and
dataclass field names are frozen — ``market_context_guide`` imports them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Dataclasses (field names frozen)
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeEntry:
    regime: str
    intent: str
    title: str
    content: str
    tags: list[str] = field(default_factory=list)


@dataclass
class ConditionalKnowledge:
    condition: str
    title: str
    content: str
    tags: list[str] = field(default_factory=list)


@dataclass
class MarketSnapshot:
    regime: str
    funding_rate: float | None = None
    open_interest_change: float | None = None
    event_alerts: list[str] = field(default_factory=list)
    trend: str = ""
    volatility: str = ""
    symbol: str = ""


# ---------------------------------------------------------------------------
# Knowledge base: key = (regime, intent)
# regime ∈ trending / ranging / volatile × intent ∈
#   open_long / open_short / close_position / stop_loss / set_risk / general
# ---------------------------------------------------------------------------


def _e(
    regime: str, intent: str, title: str, content: str, tags: list[str]
) -> KnowledgeEntry:
    return KnowledgeEntry(
        regime=regime, intent=intent, title=title, content=content, tags=tags
    )


KNOWLEDGE_BASE: dict[tuple[str, str], list[KnowledgeEntry]] = {
    ("trending", "open_long"): [
        _e(
            "trending",
            "open_long",
            "顺势做多",
            "趋势市中做多优先顺方向，回调至均线附近分批进场，避免逆势抄底。",
            ["trend", "entry"],
        ),
        _e(
            "trending",
            "open_long",
            "突破确认",
            "等待关键位放量突破再追，假突破频繁时减半仓位。",
            ["breakout"],
        ),
    ],
    ("trending", "open_short"): [
        _e(
            "trending",
            "open_short",
            "顺势做空",
            "下跌趋势中反弹到压力位做空，跌破前低后不追空。",
            ["trend", "short"],
        ),
    ],
    ("trending", "close_position"): [
        _e(
            "trending",
            "close_position",
            "趋势衰竭离场",
            "量价背离或连续长上影时主动止盈，不要等趋势完全反转。",
            ["exit"],
        ),
    ],
    ("trending", "stop_loss"): [
        _e(
            "trending",
            "stop_loss",
            "结构止损",
            "止损放在趋势结构破坏点（跌破上升趋势线/前低），而非固定百分比。",
            ["stop"],
        ),
    ],
    ("ranging", "open_long"): [
        _e(
            "ranging",
            "open_long",
            "区间下沿接多",
            "震荡市在区间下沿轻仓做多，上沿附近减仓，破位立即止损。",
            ["range", "mean-reversion"],
        ),
    ],
    ("ranging", "open_short"): [
        _e(
            "ranging",
            "open_short",
            "区间上沿做空",
            "震荡市上沿做空盈亏比最好，注意留出假突破的空间。",
            ["range", "short"],
        ),
    ],
    ("ranging", "close_position"): [
        _e(
            "ranging",
            "close_position",
            "区间中轴了结",
            "价格回到区间中轴即部分止盈，不贪区间另一端。",
            ["exit"],
        ),
    ],
    ("volatile", "set_risk"): [
        _e(
            "volatile",
            "set_risk",
            "高波动降杠杆",
            "剧烈波动行情把杠杆降到常态一半，单笔风险不超过账户 1%。",
            ["risk", "volatility"],
        ),
    ],
    ("volatile", "general"): [
        _e(
            "volatile",
            "general",
            "波动期少动",
            "波动放大阶段减少操作频率，等波动收敛后再按原计划执行。",
            ["discipline"],
        ),
    ],
    ("trending", "general"): [
        _e(
            "trending",
            "general",
            "顺势为主",
            "趋势市核心是拿住单边，减少逆势试错。",
            ["trend"],
        ),
    ],
    ("ranging", "general"): [
        _e(
            "ranging",
            "general",
            "高抛低吸",
            "震荡市以边界思维操作，中轴区域不动手。",
            ["range"],
        ),
    ],
}

CROSS_REGIME_KNOWLEDGE: list[KnowledgeEntry] = [
    _e(
        "*",
        "*",
        "资金管理铁律",
        "任何时候单笔亏损不超过总资金 2%，连亏三笔强制休息复盘。",
        ["iron-rule", "risk"],
    ),
    _e(
        "*",
        "*",
        "情绪纪律",
        "FOMO 追单和报复性交易是最大亏损来源；没有计划就不开仓。",
        ["psychology"],
    ),
]

DERIVATIVES_SIGNALS: dict[str, ConditionalKnowledge] = {
    "funding_extreme_positive": ConditionalKnowledge(
        condition="funding_rate > 0.1%",
        title="费率极正",
        content="资金费率过高说明多头拥挤，警惕插针回调，多单降杠杆或对冲。",
        tags=["funding", "contrarian"],
    ),
    "funding_extreme_negative": ConditionalKnowledge(
        condition="funding_rate < -0.1%",
        title="费率极负",
        content="负费率意味着空头拥挤，逼空风险上升，空单注意及时止盈。",
        tags=["funding"],
    ),
    "oi_surge": ConditionalKnowledge(
        condition="open_interest_change > 10%",
        title="持仓量急增",
        content="OI 快速上升伴随价格上行多为新钱推动，可跟随；价格下行则为空头进攻。",
        tags=["oi"],
    ),
    "oi_dump": ConditionalKnowledge(
        condition="open_interest_change < -10%",
        title="持仓量骤降",
        content="OI 大幅下降表明杠杆去化，行情往往进入盘整，降低预期。",
        tags=["oi"],
    ),
}

EVENT_RISK_KNOWLEDGE: list[ConditionalKnowledge] = [
    ConditionalKnowledge(
        condition="red_alert_active",
        title="红色事件在场",
        content="存在 RED 级事件告警时不开新仓，存量仓位检查止损有效性。",
        tags=["event", "iron-rule"],
    ),
    ConditionalKnowledge(
        condition="orange_alert_active",
        title="橙色事件在场",
        content="ORANGE 级事件建议仓位系数降至 0.8 以下并避开数据公布时段。",
        tags=["event"],
    ),
    ConditionalKnowledge(
        condition="vix_crisis",
        title="VIX 危机模式",
        content="VIX 进入 crisis 时全市场相关性趋近于 1，分散失效，直接降仓。",
        tags=["macro"],
    ),
]

# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); selene-ized: the region blueprint
# matrix is consolidated into one bilingual table (upstream keeps the Chinese
# versions in `src/core/market_strategy.py` and the alternate-language versions
# as inline strings in `src/market_analyzer.py`), lookups reuse
# `resolve_market`, and TW / CRYPTO blueprints are Selene-original extensions
# (upstream has neither) rendered fail-open for unknown markets.
"""Market strategy blueprints: region-specific recap frameworks.

A blueprint is the *strategy framework* a market recap prompt is built around —
positioning, principles, analysis dimensions (each with checkpoints), and an
action framework.  It is complementary to :mod:`market_context` (which carries
positive focus + negative guardrails): a blueprint says how to *analyse* a
market, market_context says which concepts *do not apply* to it.

Regions CN/HK/US/JP/KR are carried over from daily_stock_analysis (both the
Chinese and English variants).  TW and CRYPTO are Selene-original additions:
SeleneQuant spans TW and is crypto-first, and upstream has no blueprint for
either, so their content is authored here in the same shape (TW modelled on the
Asian-equity blueprints, CRYPTO on Selene's derivatives-first framing).

Pure data plus rendering, stdlib only — safe to import from any package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from selene_space_types.market_context import resolve_market


@dataclass(frozen=True)
class StrategyDimension:
    """One analysis dimension of a blueprint: what to judge and how."""

    name: str
    objective: str
    checkpoints: tuple[str, ...]


@dataclass(frozen=True)
class MarketStrategyBlueprint:
    """Region-specific market strategy blueprint for one language."""

    region: str
    title: str
    positioning: str
    principles: tuple[str, ...]
    dimensions: tuple[StrategyDimension, ...]
    action_framework: tuple[str, ...]
    lang: str = "en"

    def to_prompt_block(self) -> str:
        """Render the blueprint as a prompt block (headings follow ``lang``)."""
        if self.lang == "zh":
            heading = f"## 策略蓝图：{self.title}"
            principles_head, dimensions_head, action_head = (
                "### 策略原则",
                "### 分析维度",
                "### 行动框架",
            )
        else:
            heading = f"## Strategy Blueprint: {self.title}"
            principles_head, dimensions_head, action_head = (
                "### Strategy Principles",
                "### Analysis Dimensions",
                "### Action Framework",
            )
        principles_text = "\n".join(f"- {item}" for item in self.principles)
        action_text = "\n".join(f"- {item}" for item in self.action_framework)
        dims = []
        for dim in self.dimensions:
            checkpoints = "\n".join(f"  - {cp}" for cp in dim.checkpoints)
            dims.append(f"- {dim.name}: {dim.objective}\n{checkpoints}")
        dimensions_text = "\n".join(dims)
        return (
            f"{heading}\n"
            f"{self.positioning}\n\n"
            f"{principles_head}\n{principles_text}\n\n"
            f"{dimensions_head}\n{dimensions_text}\n\n"
            f"{action_head}\n{action_text}"
        )

    def to_markdown_block(self) -> str:
        """Render the blueprint as a report section (dimension names only)."""
        section_title = (
            "### 六、策略框架" if self.lang == "zh" else "### 6. Strategy Framework"
        )
        dims = "\n".join(
            f"- **{dim.name}**: {dim.objective}" for dim in self.dimensions
        )
        return f"{section_title}\n{dims}\n"


def _dim(name: str, objective: str, *checkpoints: str) -> StrategyDimension:
    return StrategyDimension(
        name=name, objective=objective, checkpoints=tuple(checkpoints)
    )


# --- CN ---------------------------------------------------------------------

_CN_ZH = MarketStrategyBlueprint(
    region="CN",
    lang="zh",
    title="A股市场三段式复盘策略",
    positioning="聚焦指数趋势、资金博弈与板块轮动，形成次日交易计划。",
    principles=(
        "先看指数方向，再看量能结构，最后看板块持续性。",
        "结论必须映射到仓位、节奏与风险控制动作。",
        "判断使用当日数据与近3日新闻，不臆测未验证信息。",
    ),
    dimensions=(
        _dim(
            "趋势结构",
            "判断市场处于上升、震荡还是防守阶段。",
            "上证/深证/创业板是否同向",
            "放量上涨或缩量下跌是否成立",
            "关键支撑阻力是否被突破",
        ),
        _dim(
            "资金情绪",
            "识别短线风险偏好与情绪温度。",
            "涨跌家数与涨跌停结构",
            "成交额是否扩张",
            "高位股是否出现分歧",
        ),
        _dim(
            "主线板块",
            "提炼可交易主线与规避方向。",
            "领涨板块是否具备事件催化",
            "板块内部是否有龙头带动",
            "领跌板块是否扩散",
        ),
    ),
    action_framework=(
        "进攻：指数共振上行 + 成交额放大 + 主线强化。",
        "均衡：指数分化或缩量震荡，控制仓位并等待确认。",
        "防守：指数转弱 + 领跌扩散，优先风控与减仓。",
    ),
)

_CN_EN = MarketStrategyBlueprint(
    region="CN",
    lang="en",
    title="A-share Three-Phase Recap Strategy",
    positioning="Focus on index trend, liquidity, and sector rotation to shape the next-session trading plan.",
    principles=(
        "Read index direction first, then confirm liquidity structure, and finally test sector persistence.",
        "Every conclusion must map to position sizing, trading pace, and risk-control actions.",
        "Base judgments on today's data and the latest 3-day news flow without inventing unverified information.",
    ),
    dimensions=(
        _dim(
            "Trend Structure",
            "Determine whether the market is in an uptrend, range, or defensive phase.",
            "Are the SSE, SZSE, and ChiNext moving in the same direction",
            "Is the market advancing on expanding volume or slipping on contracting volume",
            "Have key support or resistance levels been reclaimed or broken",
        ),
        _dim(
            "Liquidity & Sentiment",
            "Identify near-term risk appetite and market temperature.",
            "Advance/decline breadth and limit-up/limit-down structure",
            "Whether turnover is expanding or fading",
            "Whether high-beta leaders are showing divergence",
        ),
        _dim(
            "Leading Themes",
            "Distill tradable leadership and areas to avoid.",
            "Whether leading sectors have clear event catalysts",
            "Whether sector leaders are pulling the group higher",
            "Whether weakness is broadening across lagging sectors",
        ),
    ),
    action_framework=(
        "Offensive: indices rise in sync, turnover expands, and core themes strengthen.",
        "Balanced: index divergence or low-volume consolidation; keep sizing controlled and wait for confirmation.",
        "Defensive: indices weaken and laggards broaden; prioritize risk control and de-risking.",
    ),
)

# --- HK ---------------------------------------------------------------------

_HK_ZH = MarketStrategyBlueprint(
    region="HK",
    lang="zh",
    title="港股市场三段式复盘策略",
    positioning="聚焦恒生指数趋势、南向资金博弈与板块轮动，形成次日交易计划。",
    principles=(
        "先看恒指/恒科/国企指数方向，再看南向资金情绪，最后看板块持续性。",
        "结论必须映射到仓位、节奏与风险控制动作。",
        "判断使用当日数据与近3日新闻，不臆测未验证信息。",
    ),
    dimensions=(
        _dim(
            "趋势结构",
            "判断市场处于上升、震荡还是防守阶段。",
            "恒指/恒科/国企指数是否同向",
            "放量上涨或缩量下跌是否成立",
            "关键支撑阻力是否被突破",
        ),
        _dim(
            "资金情绪",
            "识别南向资金风险偏好与情绪温度。",
            "南向资金净流入方向与规模",
            "港元汇率与内地政策含义",
            "市场广度与龙头集中度",
        ),
        _dim(
            "主线板块",
            "提炼可交易主线与规避方向。",
            "科技/互联网平台趋势持续性",
            "金融/地产对政策转向的敏感度",
            "防御与成长因子轮动",
        ),
    ),
    action_framework=(
        "进攻：恒指共振上行 + 南向资金持续流入 + 主线强化。",
        "均衡：指数分化或缩量震荡，控制仓位并等待确认。",
        "防守：指数转弱 + 波动率上升，优先风控与减仓。",
    ),
)

_HK_EN = MarketStrategyBlueprint(
    region="HK",
    lang="en",
    title="Hong Kong Market Regime Strategy",
    positioning="Focus on HSI trend, southbound flow dynamics, and sector rotation to define next-session risk posture.",
    principles=(
        "Read market regime from HSI, HSTECH, and HSCEI alignment first.",
        "Track southbound capital flow as a key sentiment driver.",
        "Translate recap into actionable risk-on/risk-off stance with clear invalidation points.",
    ),
    dimensions=(
        _dim(
            "Trend Regime",
            "Classify the market as momentum, range, or risk-off.",
            "Are HSI/HSTECH/HSCEI directionally aligned",
            "Did volume confirm the move",
            "Are key index levels reclaimed or lost",
        ),
        _dim(
            "Capital Flows",
            "Map southbound flow and macro narrative into equity risk appetite.",
            "Southbound net flow direction and magnitude",
            "USD/HKD and China policy implications",
            "Breadth and leadership concentration",
        ),
        _dim(
            "Sector Themes",
            "Identify persistent leaders and vulnerable laggards.",
            "Tech/internet platform trend persistence",
            "Financials/property sensitivity to policy shifts",
            "Defensive vs growth factor rotation",
        ),
    ),
    action_framework=(
        "Risk-on: broad index breakout with expanding southbound participation.",
        "Neutral: mixed index signals; focus on selective relative strength.",
        "Risk-off: failed breakouts and rising volatility; prioritize capital preservation.",
    ),
)

# --- US ---------------------------------------------------------------------

_US_ZH = MarketStrategyBlueprint(
    region="US",
    lang="zh",
    title="美股市场三段式复盘策略",
    positioning="聚焦指数趋势、宏观叙事与板块轮动，给出次日风控与仓位框架。",
    principles=(
        "先看标普500、纳斯达克、道琼斯是否同向，确认主线是否一致。",
        "结合宏观与流动性指标，识别风险偏好是修复还是转弱。",
        "将复盘输出映射为“进攻/均衡/防守”动作建议，并给出明确触发失效条件。",
    ),
    dimensions=(
        _dim(
            "趋势结构",
            "明确市场处于上冲、震荡还是防守转向，判断是否存在关键支撑位背离。",
        ),
        _dim("资金与情绪", "区分宏观政策、货币面与波动率对权益风险的影响。"),
        _dim("主题线索", "识别持续性最强的主题与板块轮动是否形成可交易主线。"),
    ),
    action_framework=(
        "进攻：主板块联动上行且量能/风险位同步改善。",
        "均衡：指数分化或量能未明显放大，仓位保守执行。",
        "防守：突破失守且波动率抬升时，优先减码并保留反弹可交易性。",
    ),
)

_US_EN = MarketStrategyBlueprint(
    region="US",
    lang="en",
    title="US Market Regime Strategy",
    positioning="Focus on index trend, macro narrative, and sector rotation to define next-session risk posture.",
    principles=(
        "Read market regime from S&P 500, Nasdaq, and Dow alignment first.",
        "Separate beta move from theme-driven alpha rotation.",
        "Translate recap into actionable risk-on/risk-off stance with clear invalidation points.",
    ),
    dimensions=(
        _dim(
            "Trend Regime",
            "Classify the market as momentum, range, or risk-off.",
            "Are SPX/NDX/DJI directionally aligned",
            "Did volume confirm the move",
            "Are key index levels reclaimed or lost",
        ),
        _dim(
            "Macro & Flows",
            "Map policy/rates narrative into equity risk appetite.",
            "Treasury yield and USD implications",
            "Breadth and leadership concentration",
            "Defensive vs growth factor rotation",
        ),
        _dim(
            "Sector Themes",
            "Identify persistent leaders and vulnerable laggards.",
            "AI/semiconductor/software trend persistence",
            "Energy/financials sensitivity to macro data",
            "Volatility signals from VIX and large-cap earnings",
        ),
    ),
    action_framework=(
        "Risk-on: broad index breakout with expanding participation.",
        "Neutral: mixed index signals; focus on selective relative strength.",
        "Risk-off: failed breakouts and rising volatility; prioritize capital preservation.",
    ),
)

# --- JP ---------------------------------------------------------------------

_JP_ZH = MarketStrategyBlueprint(
    region="JP",
    lang="zh",
    title="日本市场三段式复盘策略",
    positioning="聚焦日经225、东证指数、汇率与全球风险偏好，形成次日交易计划。",
    principles=(
        "先看日经225与TOPIX是否同向，再看日元、半导体/出口链与金融股表现。",
        "把指数结论映射到仓位、节奏与风险控制动作。",
        "只基于可得指数、新闻和价格行为判断，不臆造市场广度或板块统计。",
    ),
    dimensions=(
        _dim(
            "趋势结构",
            "判断日本市场处于上攻、震荡还是防守阶段。",
            "日经225/TOPIX是否同向",
            "指数是否突破或跌破关键区间",
            "大盘权重与成长链是否共振",
        ),
        _dim(
            "宏观与汇率",
            "识别日元、利率和全球风险偏好对权益市场的影响。",
            "日元方向对出口链的影响",
            "日本央行和美债利率叙事",
            "海外科技股与半导体链映射",
        ),
        _dim(
            "主题线索",
            "提炼可延续主线与需要规避的拥挤方向。",
            "半导体/自动化/汽车链持续性",
            "金融与内需股是否轮动",
            "新闻催化是否支撑价格行为",
        ),
    ),
    action_framework=(
        "进攻：主要指数共振上行 + 外部风险偏好改善 + 主线强化。",
        "均衡：指数分化或汇率扰动，降低追涨并等待确认。",
        "防守：主要指数转弱或外部风险升温，优先控制仓位。",
    ),
)

_JP_EN = MarketStrategyBlueprint(
    region="JP",
    lang="en",
    title="Japan Market Regime Strategy",
    positioning="Focus on Nikkei 225, TOPIX, currency dynamics, and global risk appetite to define the next-session trading plan.",
    principles=(
        "Read Nikkei 225 and TOPIX alignment first, then assess yen moves, semiconductor/export chains, and financials.",
        "Translate index conclusions into position sizing, trading pace, and risk-control actions.",
        "Base judgments only on available index data, news, and price action without inventing breadth or sector statistics.",
    ),
    dimensions=(
        _dim(
            "Trend Regime",
            "Classify Japan equities as advancing, range-bound, or defensive.",
            "Are Nikkei 225 and TOPIX directionally aligned",
            "Have key index ranges been reclaimed or lost",
            "Are large-cap weights and growth chains moving together",
        ),
        _dim(
            "Macro & FX",
            "Map yen, rates, and global risk appetite into equity impact.",
            "Yen direction and implications for exporters",
            "Bank of Japan and US Treasury yield narratives",
            "Overseas technology and semiconductor read-through",
        ),
        _dim(
            "Theme Signals",
            "Identify durable leadership and crowded areas to avoid.",
            "Semiconductor, automation, and auto-chain persistence",
            "Rotation between financials and domestic-demand stocks",
            "Whether news catalysts confirm price action",
        ),
    ),
    action_framework=(
        "Risk-on: major indices rise together with improving external risk appetite and stronger leadership.",
        "Neutral: index divergence or FX disruption; avoid chasing and wait for confirmation.",
        "Risk-off: major indices weaken or external risk rises; prioritize position control.",
    ),
)

# --- KR ---------------------------------------------------------------------

_KR_ZH = MarketStrategyBlueprint(
    region="KR",
    lang="zh",
    title="韩国市场三段式复盘策略",
    positioning="聚焦 KOSPI、KOSDAQ、半导体权重与全球科技风险偏好，形成次日交易计划。",
    principles=(
        "先看 KOSPI/KOSDAQ 是否同向，再看三星电子、SK 海力士等权重线索。",
        "区分指数 beta、半导体周期和成长股风险偏好的贡献。",
        "只基于可得指数、新闻和价格行为判断，不臆造市场广度或板块统计。",
    ),
    dimensions=(
        _dim(
            "趋势结构",
            "判断韩国市场处于上攻、震荡还是防守阶段。",
            "KOSPI/KOSDAQ 是否同向",
            "权重股是否支撑指数",
            "关键支撑阻力是否被突破",
        ),
        _dim(
            "科技周期",
            "识别半导体、AI 硬件和全球科技股对韩国市场的映射。",
            "存储/半导体链新闻催化",
            "美股科技方向联动",
            "外资风险偏好变化",
        ),
        _dim(
            "主题线索",
            "提炼可延续主线与需要规避的拥挤方向。",
            "电池/汽车/互联网是否轮动",
            "KOSDAQ 成长股风险偏好",
            "新闻催化是否支撑价格行为",
        ),
    ),
    action_framework=(
        "进攻：KOSPI/KOSDAQ 共振上行 + 科技权重确认 + 外部风险偏好改善。",
        "均衡：指数或权重股分化，控制仓位并等待确认。",
        "防守：科技权重转弱或外部风险升温，优先控制回撤。",
    ),
)

_KR_EN = MarketStrategyBlueprint(
    region="KR",
    lang="en",
    title="Korea Market Regime Strategy",
    positioning="Focus on KOSPI, KOSDAQ, semiconductor heavyweights, and global technology risk appetite to define the next-session trading plan.",
    principles=(
        "Read KOSPI and KOSDAQ alignment first, then assess heavyweight signals from Samsung Electronics, SK Hynix, and related technology leaders.",
        "Separate broad index beta, semiconductor cycle exposure, and growth-stock risk appetite.",
        "Base judgments only on available index data, news, and price action without inventing breadth or sector statistics.",
    ),
    dimensions=(
        _dim(
            "Trend Regime",
            "Classify Korea equities as advancing, range-bound, or defensive.",
            "Are KOSPI and KOSDAQ directionally aligned",
            "Are heavyweight technology names supporting the indices",
            "Have key support or resistance levels been reclaimed or lost",
        ),
        _dim(
            "Technology Cycle",
            "Map semiconductor, AI hardware, and global technology moves into Korea equity risk.",
            "Memory and semiconductor-chain catalysts",
            "US technology-market read-through",
            "Foreign investor risk appetite signals",
        ),
        _dim(
            "Theme Signals",
            "Identify durable leadership and crowded areas to avoid.",
            "Rotation across batteries, autos, and internet platforms",
            "KOSDAQ growth-stock risk appetite",
            "Whether news catalysts confirm price action",
        ),
    ),
    action_framework=(
        "Risk-on: KOSPI and KOSDAQ rise together with confirmed technology leadership and improving external risk appetite.",
        "Neutral: index or heavyweight divergence; keep sizing controlled and wait for confirmation.",
        "Risk-off: technology heavyweights weaken or external risk rises; prioritize drawdown control.",
    ),
)

# --- TW (Selene-original extension) -----------------------------------------

_TW_ZH = MarketStrategyBlueprint(
    region="TW",
    lang="zh",
    title="台股市场三段式复盘策略",
    positioning="聚焦加权指数趋势、三大法人动向与半导体产业链，形成次日交易计划。",
    principles=(
        "先看加权指数与柜买指数是否同向，再看三大法人买卖超，最后看半导体链持续性。",
        "结论必须映射到仓位、节奏与风险控制动作。",
        "只基于可得指数、法人数据、新闻和价格行为判断，不臆造未验证信息。",
    ),
    dimensions=(
        _dim(
            "趋势结构",
            "判断台股处于上攻、震荡还是防守阶段。",
            "加权指数/柜买指数是否同向",
            "放量上涨或缩量下跌是否成立",
            "关键支撑阻力是否被突破",
        ),
        _dim(
            "法人资金",
            "识别三大法人与当冲的风险偏好。",
            "外资/投信/自营商买卖超方向",
            "新台币汇率与资金流向",
            "当冲比与市场广度",
        ),
        _dim(
            "主线板块",
            "提炼可交易主线与规避方向。",
            "半导体/电子代工链趋势持续性",
            "AI 服务器与高速运算需求映射",
            "领跌板块是否扩散",
        ),
    ),
    action_framework=(
        "进攻：加权指数共振上行 + 法人同步买超 + 半导体链强化。",
        "均衡：指数分化或量能未放大，控制仓位并等待确认。",
        "防守：指数转弱 + 法人转卖，优先风控与减仓。",
    ),
)

_TW_EN = MarketStrategyBlueprint(
    region="TW",
    lang="en",
    title="Taiwan Market Regime Strategy",
    positioning="Focus on TAIEX trend, three-institutional-investor flows, and the semiconductor chain to define the next-session trading plan.",
    principles=(
        "Read TAIEX and TPEx alignment first, then assess the three institutional investor groups' net flow, and finally the semiconductor chain.",
        "Translate index conclusions into position sizing, trading pace, and risk-control actions.",
        "Base judgments only on available index, institutional, news, and price-action data without inventing statistics.",
    ),
    dimensions=(
        _dim(
            "Trend Regime",
            "Classify Taiwan equities as advancing, range-bound, or defensive.",
            "Are TAIEX and TPEx directionally aligned",
            "Is the market advancing on expanding volume or slipping on contracting volume",
            "Have key support or resistance levels been reclaimed or broken",
        ),
        _dim(
            "Institutional Flows",
            "Identify risk appetite from the three institutional investor groups and day trading.",
            "Foreign / SITC / dealer net buy-sell direction",
            "TWD FX and cross-border flow",
            "Day-trading share and market breadth",
        ),
        _dim(
            "Sector Themes",
            "Identify durable leadership and vulnerable laggards.",
            "Semiconductor / electronics-foundry chain persistence",
            "AI server and high-performance-computing demand read-through",
            "Whether weakness is broadening across lagging sectors",
        ),
    ),
    action_framework=(
        "Risk-on: TAIEX rises in sync with institutional net buying and a strengthening semiconductor chain.",
        "Neutral: index divergence or fading volume; keep sizing controlled and wait for confirmation.",
        "Risk-off: index weakens with institutions turning net sellers; prioritize risk control and de-risking.",
    ),
)

# --- CRYPTO (Selene-original extension) -------------------------------------

_CRYPTO_ZH = MarketStrategyBlueprint(
    region="CRYPTO",
    lang="zh",
    title="加密市场三段式复盘策略",
    positioning="聚焦 BTC/ETH 趋势、资金费率与板块轮动，形成次轮交易计划（7×24）。",
    principles=(
        "先看 BTC/ETH 方向与主导率，再看资金费率与未平仓量，最后看板块轮动。",
        "结论必须映射到仓位、杠杆与风险控制动作。",
        "只基于链上/衍生品数据、新闻和价格行为判断，不臆造未验证信息。",
    ),
    dimensions=(
        _dim(
            "趋势结构",
            "判断市场处于上攻、震荡还是防守阶段。",
            "BTC/ETH 是否同向",
            "主导率（BTC dominance）变化",
            "关键支撑阻力是否被突破",
        ),
        _dim(
            "衍生品与资金",
            "识别杠杆拥挤度与风险偏好。",
            "资金费率是否极端",
            "未平仓量与多空比变化",
            "爆仓规模与现货溢价/折价",
        ),
        _dim(
            "板块轮动",
            "提炼可延续主线与规避方向。",
            "主流币与山寨币轮动",
            "链上活跃度与叙事催化",
            "领跌板块是否扩散",
        ),
    ),
    action_framework=(
        "进攻：BTC/ETH 共振上行 + 资金费率健康 + 板块轮动扩散。",
        "均衡：趋势分化或资金费率转负，降低杠杆并等待确认。",
        "防守：趋势转弱 + 爆仓放大，优先降杠杆与控回撤。",
    ),
)

_CRYPTO_EN = MarketStrategyBlueprint(
    region="CRYPTO",
    lang="en",
    title="Crypto Market Regime Strategy",
    positioning="Focus on BTC/ETH trend, funding rate, and sector rotation to define the next-session trading plan (24/7).",
    principles=(
        "Read BTC/ETH direction and dominance first, then funding rate and open interest, and finally sector rotation.",
        "Every conclusion must map to position sizing, leverage, and risk-control actions.",
        "Base judgments only on on-chain / derivatives data, news, and price action without inventing unverified information.",
    ),
    dimensions=(
        _dim(
            "Trend Regime",
            "Classify the market as momentum, range, or risk-off.",
            "Are BTC and ETH directionally aligned",
            "BTC dominance shifts",
            "Have key support or resistance levels been reclaimed or broken",
        ),
        _dim(
            "Derivatives & Flows",
            "Identify leverage crowding and risk appetite.",
            "Whether funding rate is extreme",
            "Open-interest and long/short-ratio shifts",
            "Liquidation size and spot premium/discount",
        ),
        _dim(
            "Sector Rotation",
            "Distill durable leadership and crowded areas to avoid.",
            "Rotation between majors and altcoins",
            "On-chain activity and narrative catalysts",
            "Whether weakness is broadening across lagging sectors",
        ),
    ),
    action_framework=(
        "Risk-on: BTC/ETH rise in sync with healthy funding and broadening rotation.",
        "Neutral: trend divergence or funding turning negative; cut leverage and wait for confirmation.",
        "Risk-off: trend weakens with liquidation spikes; prioritize deleveraging and drawdown control.",
    ),
)


_BLUEPRINTS: Final[dict[str, dict[str, MarketStrategyBlueprint]]] = {
    "CN": {"zh": _CN_ZH, "en": _CN_EN},
    "HK": {"zh": _HK_ZH, "en": _HK_EN},
    "US": {"zh": _US_ZH, "en": _US_EN},
    "JP": {"zh": _JP_ZH, "en": _JP_EN},
    "KR": {"zh": _KR_ZH, "en": _KR_EN},
    "TW": {"zh": _TW_ZH, "en": _TW_EN},
    "CRYPTO": {"zh": _CRYPTO_ZH, "en": _CRYPTO_EN},
}


def _lang_key(lang: str | None) -> str:
    return "zh" if (lang or "").strip().lower().startswith("zh") else "en"


def strategy_blueprint(
    market: str | None, lang: str = "en"
) -> MarketStrategyBlueprint | None:
    """Return the blueprint for a market, or None when unrecognized."""
    key = resolve_market(market)
    if key is None:
        return None
    return _BLUEPRINTS[key][_lang_key(lang)]


def render_strategy_blueprint(
    market: str | None, lang: str = "en", *, markdown: bool = False
) -> str:
    """Render a blueprint prompt block, or "" for an unknown market.

    Fail-open by design (mirrors :func:`render_market_guidance`): an
    unrecognized market yields an empty string rather than a guess, so a caller
    can concatenate the result unconditionally.  Set ``markdown=True`` for the
    short report-section form (dimension names only).
    """
    blueprint = strategy_blueprint(market, lang)
    if blueprint is None:
        return ""
    return blueprint.to_markdown_block() if markdown else blueprint.to_prompt_block()

"""
Factor definitions mapping AInvest strategies to A-share implementable factors.

Each factor has:
- name: short identifier
- category: technical / fundamental / sentiment / event
- subcategory: finer grouping
- description: what the factor measures
- ainvest_source: which AInvest strategy inspired this
- calc_requires: data fields needed for calculation
- direction: 1 = higher is bullish, -1 = higher is bearish
- warmup_bars: bars of history needed before the value is meaningful

``warmup_bars`` is declared for every factor computed by
``factor_calc.TECHNICAL_CALCULATORS`` and consumed by
:mod:`selene_space_ashare.factor_warmup`, which NaNs out the leading rows at
the panel-assembly layer instead of letting a short series produce a
fabricated value.
Non-technical factors keep the field's default of 1 (point-in-time or
cross-sectional — no rolling window, nothing to warm up).
"""

from selene_space_types.stock import FactorCategory, FactorDef


# ─── TECHNICAL: Momentum / Trend ───────────────────────────────────

MOMENTUM_1M = FactorDef(
    name="MOMENTUM_1M",
    category=FactorCategory.TECHNICAL,
    subcategory="momentum",
    description="1-month price return (21 trading days)",
    ainvest_source=[
        "high-relative-strength-stocks",
        "pullback-leaders-momentum-stocks",
    ],
    calc_requires=["close"],
    direction=1,
    warmup_bars=21,
)

MOMENTUM_3M = FactorDef(
    name="MOMENTUM_3M",
    category=FactorCategory.TECHNICAL,
    subcategory="momentum",
    description="3-month price return (63 trading days)",
    ainvest_source=["high-relative-strength-stocks", "recent-breakout-stocks"],
    calc_requires=["close"],
    direction=1,
    warmup_bars=63,
)

MOMENTUM_6M = FactorDef(
    name="MOMENTUM_6M",
    category=FactorCategory.TECHNICAL,
    subcategory="momentum",
    description="6-month price return (126 trading days)",
    ainvest_source=["52-week-high-stocks", "steady-climbers-momentum-stocks"],
    calc_requires=["close"],
    direction=1,
    warmup_bars=126,
)

MOMENTUM_12M = FactorDef(
    name="MOMENTUM_12M",
    category=FactorCategory.TECHNICAL,
    subcategory="momentum",
    description="12-month price return (252 trading days)",
    ainvest_source=["52-week-high-stocks"],
    calc_requires=["close"],
    direction=1,
    warmup_bars=252,
)

HIGH_52W_PROXIMITY = FactorDef(
    name="HIGH_52W_PROXIMITY",
    category=FactorCategory.TECHNICAL,
    subcategory="momentum",
    description="Proximity to 52-week high (close / max_252d), higher = closer to high",
    ainvest_source=["52-week-high-stocks"],
    calc_requires=["close"],
    direction=1,
    warmup_bars=252,
)

# ─── TECHNICAL: Volatility ─────────────────────────────────────────

VOLATILITY_20D = FactorDef(
    name="VOLATILITY_20D",
    category=FactorCategory.TECHNICAL,
    subcategory="volatility",
    description="20-day realized volatility (std of daily returns)",
    ainvest_source=["volatile-tech-stocks", "volatility-squeeze-setups"],
    calc_requires=["close"],
    direction=-1,  # low vol = quality
    warmup_bars=20,
)

BB_WIDTH = FactorDef(
    name="BB_WIDTH",
    category=FactorCategory.TECHNICAL,
    subcategory="volatility",
    description="Bollinger Band width (upper - lower) / middle, measures squeeze",
    ainvest_source=["bollinger-band-breakout-stocks", "volatility-squeeze-setups"],
    calc_requires=["close"],
    direction=1,  # breakout after squeeze
    warmup_bars=20,
)

ATR_RATIO = FactorDef(
    name="ATR_RATIO",
    category=FactorCategory.TECHNICAL,
    subcategory="volatility",
    description="ATR(14) / close price, normalized true range",
    ainvest_source=["volatility-squeeze-setups"],
    calc_requires=["high", "low", "close"],
    direction=1,
    warmup_bars=14,
)

RESID_VOL_60D = FactorDef(
    name="RESID_VOL_60D",
    category=FactorCategory.TECHNICAL,
    subcategory="volatility",
    description="60d rolling residual vol of stock return vs CSI300 index return, annualized",
    ainvest_source=[],
    calc_requires=["close", "index_close"],
    direction=-1,  # low residual vol = quality (same as VOLATILITY_20D)
)

# ─── TECHNICAL: Mean Reversion / Overextension ─────────────────────

PRICE_ZSCORE_20D = FactorDef(
    name="PRICE_ZSCORE_20D",
    category=FactorCategory.TECHNICAL,
    subcategory="mean_reversion",
    description="Z-score of close vs 20-day mean, measures overextension",
    ainvest_source=["mean-reversion-plays", "oversold-rebound-stocks"],
    calc_requires=["close"],
    direction=-1,  # extreme negative = oversold bounce
    warmup_bars=20,
)

MA_DEVIATION = FactorDef(
    name="MA_DEVIATION",
    category=FactorCategory.TECHNICAL,
    subcategory="mean_reversion",
    description="(close - MA20) / MA20, deviation from 20-day moving average",
    ainvest_source=["moving-average-bounce-plays", "oversold-rebound-stocks"],
    calc_requires=["close"],
    direction=-1,
    warmup_bars=20,
)

RSI_14D = FactorDef(
    name="RSI_14D",
    category=FactorCategory.TECHNICAL,
    subcategory="mean_reversion",
    description="14-day RSI, overbought >70 / oversold <30",
    ainvest_source=["rsi-oversold-reversal-stocks", "oversold-rebound-stocks"],
    calc_requires=["close"],
    direction=-1,  # oversold bounce (but AInvest shows -88% return! use with trend filter)
    warmup_bars=14,
)

# ─── TECHNICAL: MACD / Crossover ───────────────────────────────────

MACD_SIGNAL = FactorDef(
    name="MACD_SIGNAL",
    category=FactorCategory.TECHNICAL,
    subcategory="crossover",
    description="MACD line - signal line, positive = bullish crossover",
    ainvest_source=["macd-bullish-crossover-stocks"],
    calc_requires=["close"],
    direction=1,
    warmup_bars=26,
)

GOLDEN_CROSS = FactorDef(
    name="GOLDEN_CROSS",
    category=FactorCategory.TECHNICAL,
    subcategory="crossover",
    description="MA5 > MA20 > MA60 alignment, 1=full golden cross, 0=none",
    ainvest_source=["golden-cross-stocks"],
    calc_requires=["close"],
    direction=1,
    warmup_bars=60,
)

# ─── TECHNICAL: Volume-Price ───────────────────────────────────────

VOLUME_ZSCORE = FactorDef(
    name="VOLUME_ZSCORE",
    category=FactorCategory.TECHNICAL,
    subcategory="volume_price",
    description="Z-score of volume vs 20-day mean, detects unusual activity",
    ainvest_source=["unusual-volume-flow-stocks", "high-volume-gainers"],
    calc_requires=["close", "volume"],
    direction=1,  # high volume = institutional interest
    warmup_bars=20,
)

VOLUME_PRICE_TREND = FactorDef(
    name="VOLUME_PRICE_TREND",
    category=FactorCategory.TECHNICAL,
    subcategory="volume_price",
    description="OBV slope (20-day), rising OBV = accumulation",
    ainvest_source=["high-volume-uptrend-stocks"],
    calc_requires=["close", "volume"],
    direction=1,
    warmup_bars=20,
)

VOLUME_CLIMAX = FactorDef(
    name="VOLUME_CLIMAX",
    category=FactorCategory.TECHNICAL,
    subcategory="volume_price",
    description="Volume > 3x 20-day avg + wide range candle, exhaustion signal",
    ainvest_source=["volume-climax-reversal-stocks"],
    calc_requires=["high", "low", "close", "volume"],
    direction=-1,  # climax = reversal
    warmup_bars=20,
)

CROWDING_SCORE = FactorDef(
    name="CROWDING_SCORE",
    category=FactorCategory.TECHNICAL,
    subcategory="volume_price",
    description="Cross-sectional crowding: amount rank x turnover Z (composite from A-share multifactor system)",
    ainvest_source=[],
    calc_requires=["amount", "turnover"],
    direction=-1,  # crowded = reversal risk (same logic as VOLUME_CLIMAX)
)

# ─── FUNDAMENTAL: Value ────────────────────────────────────────────

PE_PERCENTILE = FactorDef(
    name="PE_PERCENTILE",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="value",
    description="PE ratio percentile rank in 3-year history, low = undervalued",
    ainvest_source=[
        "undervalued-blue-chips",
        "low-pe-stocks",
        "turnaround-value-stocks",
    ],
    calc_requires=["pe_ttm"],
    direction=-1,
)

PB_PERCENTILE = FactorDef(
    name="PB_PERCENTILE",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="value",
    description="PB ratio percentile rank in 3-year history, low = deep value",
    ainvest_source=["asset-backed-deep-value-stocks", "turnaround-value-stocks"],
    calc_requires=["pb"],
    direction=-1,
)

FCF_YIELD = FactorDef(
    name="FCF_YIELD",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="value",
    description="Free cash flow / market cap, higher = cheaper",
    ainvest_source=["high-free-cash-flow-yield-stocks"],
    calc_requires=["fcf", "market_cap"],
    direction=1,
)

SHAREHOLDER_YIELD = FactorDef(
    name="SHAREHOLDER_YIELD",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="value",
    description="Dividend yield + buyback yield, total shareholder return",
    ainvest_source=["shareholder-yield-value-stocks"],
    calc_requires=["dividend_yield", "buyback_yield"],
    direction=1,
)

EP = FactorDef(
    name="EP",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="value",
    description="Earnings-to-price: epsTTM (latest announced quarter, PIT via merge_asof pubDate) / current close. 分母用当日 close（PIT 安全），分子用 merge_asof 后财报",
    ainvest_source=[],
    calc_requires=["epsTTM", "close"],
    direction=1,
)

BP = FactorDef(
    name="BP",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="value",
    description=(
        "Book-to-price: 1/pbMRQ (daily valuation field from kline data; "
        "mathematically equivalent to BP). 季频净资产口径不可行 — "
        "baostock query_balance_data 是偿债比率表无净资产绝对值字段。"
        "原项目先例 expression_factors.py:171"
    ),
    ainvest_source=[],
    calc_requires=["pbMRQ", "close"],
    direction=1,
)

# ─── FUNDAMENTAL: Growth ───────────────────────────────────────────

REVENUE_GROWTH_YOY = FactorDef(
    name="REVENUE_GROWTH_YOY",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="growth",
    description="Revenue year-over-year growth rate",
    ainvest_source=["high-revenue-growth-stocks", "profitable-growth-tech-stocks"],
    calc_requires=["revenue_yoy"],
    direction=1,
)

EARNINGS_SURPRISE = FactorDef(
    name="EARNINGS_SURPRISE",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="growth",
    description="Actual EPS - consensus EPS, positive = beat expectations",
    ainvest_source=[
        "consistent-earnings-surprise-growth-stocks",
        "earnings-beats-misses",
    ],
    calc_requires=["eps_actual", "eps_estimate"],
    direction=1,
)

ROE = FactorDef(
    name="ROE",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="growth",
    description="Return on equity (TTM), measures profitability",
    ainvest_source=["high-margin-growth-compounders", "profitable-growth-tech-stocks"],
    calc_requires=["roe"],
    direction=1,
)

GROSS_MARGIN = FactorDef(
    name="GROSS_MARGIN",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="growth",
    description="Gross profit margin, higher = pricing power",
    ainvest_source=["high-margin-growth-compounders"],
    calc_requires=["gross_margin"],
    direction=1,
)

RD_RATIO = FactorDef(
    name="RD_RATIO",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="growth",
    description="R&D expense / revenue, innovation investment",
    ainvest_source=["high-rd-investment-innovators"],
    calc_requires=["rd_expense", "revenue"],
    direction=1,
)

YOY_NI = FactorDef(
    name="YOY_NI",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="growth",
    description="Net profit YoY growth from latest announced quarterly report (baostock YOYNI, PIT via merge_asof pubDate)",
    ainvest_source=[],
    calc_requires=["YOYNI"],
    direction=1,
)

GP_MARGIN = FactorDef(
    name="GP_MARGIN",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="growth",
    description=(
        "Gross profit margin from latest announced quarterly report "
        "(eastmoney XSMLL, PIT via merge_asof pubDate). 毛利率定价权先验方向为正。"
    ),
    ainvest_source=[],
    calc_requires=["gpMargin"],
    direction=1,
)

# ─── FUNDAMENTAL: Leverage / Size ──────────────────────────────────

LEVERAGE = FactorDef(
    name="LEVERAGE",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="leverage",
    description=(
        "Debt-to-asset ratio from latest announced quarterly report "
        "(eastmoney DEBT_ASSET_RATIO, PIT via merge_asof pubDate). "
        "低杠杆=质量先验，方向为负。"
    ),
    ainvest_source=[],
    calc_requires=["liabilityToAsset"],
    direction=-1,
)

CURRENT_RATIO = FactorDef(
    name="CURRENT_RATIO",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="leverage",
    description=(
        "Current ratio (流动比率) from latest announced quarterly report "
        "(eastmoney CURRENT_RATIO, PIT via merge_asof pubDate). "
        "流动性稳健性先验方向为正。"
    ),
    ainvest_source=[],
    calc_requires=["currentRatio"],
    direction=1,
)

SIZE = FactorDef(
    name="SIZE",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="size",
    description=(
        "ln(float market cap): float_shares = volume / (turnover/100) derived "
        "from daily klines (turnover is %), mktcap = float_shares x close. "
        "口径=流通市值（非总市值，A股可交易盘）；小市值溢价经典方向为负。"
        "纯 K 线列派生无 PIT 风险。"
    ),
    ainvest_source=[],
    calc_requires=["volume", "turnover", "close"],
    direction=-1,
)

# ─── FUNDAMENTAL: Dividend ─────────────────────────────────────────

DIVIDEND_YIELD = FactorDef(
    name="DIVIDEND_YIELD",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="dividend",
    description="Trailing 12-month dividend yield",
    ainvest_source=["high-dividend-yield-stocks", "reit-dividend-leaders"],
    calc_requires=["dividend_yield"],
    direction=1,
)

DIVIDEND_GROWTH_3Y = FactorDef(
    name="DIVIDEND_GROWTH_3Y",
    category=FactorCategory.FUNDAMENTAL,
    subcategory="dividend",
    description="3-year dividend CAGR",
    ainvest_source=["low-payout-dividend-growth-stocks", "us-dividend-growth-leaders"],
    calc_requires=["dividend_history"],
    direction=1,
)

# ─── SENTIMENT: Money Flow ─────────────────────────────────────────

NORTHBOUND_FLOW = FactorDef(
    name="NORTHBOUND_FLOW",
    category=FactorCategory.SENTIMENT,
    subcategory="money_flow",
    description="Net northbound (沪股通+深股通) buying amount, institutional signal",
    ainvest_source=[
        "high-institutional-ownership-stocks",
        "retail-vs-institutional-flow",
    ],
    calc_requires=["northbound_net"],
    direction=1,
)

MARGIN_BALANCE_RATIO = FactorDef(
    name="MARGIN_BALANCE_RATIO",
    category=FactorCategory.SENTIMENT,
    subcategory="money_flow",
    description="融资余额 / 流通市值, high = crowded long",
    ainvest_source=["high-short-interest-stocks", "extreme-put-call-ratio"],
    calc_requires=["margin_balance", "float_cap"],
    direction=-1,  # extreme = reversal risk
)

ANALYST_RATING = FactorDef(
    name="ANALYST_RATING",
    category=FactorCategory.SENTIMENT,
    subcategory="analyst",
    description="Consensus analyst rating score (1=strong buy, 5=strong sell)",
    ainvest_source=["top-analyst-upgrades", "new-buy-ratings-stocks"],
    calc_requires=["analyst_rating"],
    direction=-1,  # lower score = more bullish
)

# ─── EVENT: Earnings / Corporate ───────────────────────────────────

POST_EARNINGS_MOMENTUM = FactorDef(
    name="POST_EARNINGS_MOMENTUM",
    category=FactorCategory.EVENT,
    subcategory="earnings",
    description="5-day return after earnings announcement, PEAD effect",
    ainvest_source=["post-earnings-momentum-stocks", "earnings-gap-swing-trades"],
    calc_requires=["earnings_date", "close"],
    direction=1,
)

INSIDER_BUY_SIGNAL = FactorDef(
    name="INSIDER_BUY_SIGNAL",
    category=FactorCategory.EVENT,
    subcategory="corporate",
    description="龙虎榜/大宗交易净买入 signal, 1=net buy, 0=none",
    ainvest_source=["heavy-insider-buying-stocks", "ai-insider-activity-signal-stocks"],
    calc_requires=["dragon_tiger_net"],
    direction=1,
)

BUYBACK_SIGNAL = FactorDef(
    name="BUYBACK_SIGNAL",
    category=FactorCategory.EVENT,
    subcategory="corporate",
    description="Recent share buyback announcement, 1=yes, 0=no",
    ainvest_source=["share-buyback-announcement-stocks"],
    calc_requires=["buyback_announcement"],
    direction=1,
)


# ─── Factor Registry ───────────────────────────────────────────────

ALL_FACTORS: dict[str, FactorDef] = {
    f.name: f
    for f in [
        # Momentum
        MOMENTUM_1M,
        MOMENTUM_3M,
        MOMENTUM_6M,
        MOMENTUM_12M,
        HIGH_52W_PROXIMITY,
        # Volatility
        VOLATILITY_20D,
        BB_WIDTH,
        ATR_RATIO,
        RESID_VOL_60D,
        # Mean Reversion
        PRICE_ZSCORE_20D,
        MA_DEVIATION,
        RSI_14D,
        # Crossover
        MACD_SIGNAL,
        GOLDEN_CROSS,
        # Volume-Price
        VOLUME_ZSCORE,
        VOLUME_PRICE_TREND,
        VOLUME_CLIMAX,
        CROWDING_SCORE,
        # Value
        PE_PERCENTILE,
        PB_PERCENTILE,
        FCF_YIELD,
        SHAREHOLDER_YIELD,
        EP,
        BP,
        # Growth
        REVENUE_GROWTH_YOY,
        EARNINGS_SURPRISE,
        ROE,
        GROSS_MARGIN,
        RD_RATIO,
        YOY_NI,
        GP_MARGIN,
        # Leverage / Size
        LEVERAGE,
        CURRENT_RATIO,
        SIZE,
        # Dividend
        DIVIDEND_YIELD,
        DIVIDEND_GROWTH_3Y,
        # Sentiment
        NORTHBOUND_FLOW,
        MARGIN_BALANCE_RATIO,
        ANALYST_RATING,
        # Event
        POST_EARNINGS_MOMENTUM,
        INSIDER_BUY_SIGNAL,
        BUYBACK_SIGNAL,
    ]
}


def get_factors_by_category(category: FactorCategory) -> dict[str, FactorDef]:
    return {n: f for n, f in ALL_FACTORS.items() if f.category == category}


def get_factors_by_subcategory(subcategory: str) -> dict[str, FactorDef]:
    return {n: f for n, f in ALL_FACTORS.items() if f.subcategory == subcategory}


def get_technical_factors() -> dict[str, FactorDef]:
    return get_factors_by_category(FactorCategory.TECHNICAL)


def get_fundamental_factors() -> dict[str, FactorDef]:
    return get_factors_by_category(FactorCategory.FUNDAMENTAL)


def get_sentiment_factors() -> dict[str, FactorDef]:
    return get_factors_by_category(FactorCategory.SENTIMENT)


def get_event_factors() -> dict[str, FactorDef]:
    return get_factors_by_category(FactorCategory.EVENT)

# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); selene-ized: per-market positive
# focus plus explicit negative guardrails ("do NOT apply") for multi-market
# LLM prompts, rendered fail-open for unknown markets.
"""Market-specific prompt context: positive focus + negative guardrails.

DSA guards its prompts by naming the market under analysis and the concepts
the model must *not* import from another market — e.g. "do not apply A-share
price-limit / Northbound / Dragon-Tiger concepts to a Japan stock".

SeleneQuant is crypto-first and spans CN/HK/US/JP/KR/TW/CRYPTO, so the risk
runs both ways: A-share concepts leaking onto foreign equities, *and* crypto
concepts (funding rate, perpetuals, 24/7) leaking onto equities. Both
directions are carried here as data.

Pure functions, stdlib only — safe to import from any package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class MarketGuidance:
    """Positive focus and negative guardrails for one market and language."""

    focus: tuple[str, ...]
    avoid: tuple[str, ...]


_MARKET_LABEL: Final[dict[str, dict[str, str]]] = {
    "CN": {
        "zh": "A 股（中国沪深交易所上市股票）",
        "en": "China A-share (Shanghai/Shenzhen listed)",
    },
    "HK": {
        "zh": "港股（香港交易所上市股票）",
        "en": "Hong Kong stock (HKEX listed)",
    },
    "US": {
        "zh": "美股（NYSE/NASDAQ 上市股票）",
        "en": "US stock (NYSE/NASDAQ listed)",
    },
    "JP": {
        "zh": "日股（日本交易所，Yahoo 后缀如 .T）",
        "en": "Japan stock (Yahoo suffix such as .T)",
    },
    "KR": {
        "zh": "韩股（KOSPI/KOSDAQ，后缀 .KS/.KQ）",
        "en": "Korea stock (KOSPI/KOSDAQ, .KS/.KQ)",
    },
    "TW": {
        "zh": "台股（TWSE .TW / TPEx 上柜 .TWO）",
        "en": "Taiwan stock (TWSE .TW / TPEx .TWO)",
    },
    "CRYPTO": {
        "zh": "加密货币（现货/永续合约，7×24 交易）",
        "en": "Crypto (spot/perpetual futures, 24/7)",
    },
}

_GUIDANCE: Final[dict[str, dict[str, MarketGuidance]]] = {
    "CN": {
        "zh": MarketGuidance(
            focus=(
                "涨跌停机制（±10%/±20%/±30%）",
                "T+1 交易制度",
                "北向资金、龙虎榜、融资融券",
                "产业政策与监管因素",
            ),
            avoid=(
                "港股/美股的 T+0 与『无涨跌停』假设",
                "加密货币的资金费率、永续合约、爆仓、7×24 交易等概念",
            ),
        ),
        "en": MarketGuidance(
            focus=(
                "daily price limits (±10%/±20%/±30%)",
                "T+1 settlement",
                "Northbound flows, Dragon-Tiger lists, margin financing",
                "industrial policy and regulatory factors",
            ),
            avoid=(
                "HK/US T+0 and 'no price limit' assumptions",
                "crypto concepts such as funding rate, perpetuals, liquidations, 24/7 trading",
            ),
        ),
    },
    "HK": {
        "zh": MarketGuidance(
            focus=(
                "港币（HKD）汇率",
                "南向/北向资金流",
                "港股无涨跌停、支持 T+0",
                "联交所特有规则",
            ),
            avoid=(
                "A 股专属的涨跌停板与龙虎榜（港股资金流口径不同）",
                "加密货币的永续合约与资金费率概念",
            ),
        ),
        "en": MarketGuidance(
            focus=(
                "HKD FX",
                "Southbound/Northbound flows",
                "no daily price limits, T+0 trading",
                "HKEX-specific rules",
            ),
            avoid=(
                "A-share-specific price-limit boards and Dragon-Tiger lists",
                "crypto perpetual / funding-rate concepts",
            ),
        ),
    },
    "US": {
        "zh": MarketGuidance(
            focus=(
                "美元（USD）汇率",
                "美联储政策与 SEC 监管",
                "T+0、盘前盘后交易与熔断机制",
                "财报季与回购",
            ),
            avoid=(
                "A 股专属的涨跌停、北向资金、龙虎榜、融资融券",
                "加密货币的资金费率与永续合约概念",
            ),
        ),
        "en": MarketGuidance(
            focus=(
                "USD FX",
                "Fed policy and SEC regulation",
                "T+0, pre/after-market sessions, circuit breakers",
                "earnings season and buybacks",
            ),
            avoid=(
                "A-share price limits, Northbound flows, Dragon-Tiger lists",
                "crypto funding-rate / perpetual concepts",
            ),
        ),
    },
    "JP": {
        "zh": MarketGuidance(
            focus=(
                "日元（JPY）汇率",
                "日本央行政策",
                "公司治理与行业周期",
            ),
            avoid=(
                "A 股专属的涨跌停、北向资金、龙虎榜、融资融券等概念",
                "加密货币的永续合约与资金费率概念",
            ),
        ),
        "en": MarketGuidance(
            focus=(
                "JPY FX",
                "BOJ policy",
                "corporate governance and sector cycles",
            ),
            avoid=(
                "A-share concepts such as price limits, Northbound flows, "
                "Dragon-Tiger lists, margin financing",
                "crypto perpetual / funding-rate concepts",
            ),
        ),
    },
    "KR": {
        "zh": MarketGuidance(
            focus=(
                "韩元（KRW）汇率",
                "韩国央行政策",
                "半导体/互联网产业周期",
                "韩国交易制度",
            ),
            avoid=(
                "A 股专属的涨跌停、北向资金、龙虎榜、融资融券等概念",
                "加密货币的永续合约与资金费率概念",
            ),
        ),
        "en": MarketGuidance(
            focus=(
                "KRW FX",
                "Bank of Korea policy",
                "semiconductor / internet cycles",
                "local trading rules",
            ),
            avoid=(
                "A-share concepts such as price limits, Northbound flows, "
                "Dragon-Tiger lists",
                "crypto perpetual / funding-rate concepts",
            ),
        ),
    },
    "TW": {
        "zh": MarketGuidance(
            focus=(
                "新台币（TWD）汇率",
                "台湾央行政策",
                "半导体/电子代工产业链",
                "三大法人（外资／投信／自营商）买卖超、当冲",
                "TWSE/TPEx ±10% 涨跌停",
            ),
            avoid=(
                "A 股专属的北向资金、龙虎榜等概念（台股法人结构与资金流口径不同）",
                "加密货币的永续合约与资金费率概念",
            ),
        ),
        "en": MarketGuidance(
            focus=(
                "TWD FX",
                "Central Bank of the ROC policy",
                "semiconductor / electronics-foundry supply chain",
                "three institutional investor groups, day trading",
                "TWSE/TPEx ±10% price limit",
            ),
            avoid=(
                "A-share concepts such as Northbound flows or Dragon-Tiger lists",
                "crypto perpetual / funding-rate concepts",
            ),
        ),
    },
    "CRYPTO": {
        "zh": MarketGuidance(
            focus=(
                "资金费率（funding rate）与未平仓量（open interest）",
                "现货 vs 永续合约",
                "爆仓/强平与杠杆",
                "7×24 交易、无涨跌停",
            ),
            avoid=(
                "股票的涨跌停、T+1 交割、财报季、交易所开收盘时段等概念",
                "A 股专属的北向资金、龙虎榜、融资融券等概念",
            ),
        ),
        "en": MarketGuidance(
            focus=(
                "funding rate and open interest",
                "spot vs perpetual futures",
                "liquidations and leverage",
                "24/7 trading, no price limits",
            ),
            avoid=(
                "equity concepts such as daily price limits, T+1 settlement, "
                "earnings season, or exchange session hours",
                "A-share-specific Northbound flows, Dragon-Tiger lists, "
                "margin financing",
            ),
        ),
    },
}

_ALIASES: Final[dict[str, str]] = {
    "cn": "CN",
    "ashare": "CN",
    "a_share": "CN",
    "china": "CN",
    "sh": "CN",
    "sz": "CN",
    "hk": "HK",
    "hongkong": "HK",
    "hong_kong": "HK",
    "hsi": "HK",
    "us": "US",
    "usa": "US",
    "us_stock": "US",
    "nasdaq": "US",
    "nyse": "US",
    "jp": "JP",
    "japan": "JP",
    "tse": "JP",
    "kr": "KR",
    "korea": "KR",
    "kospi": "KR",
    "kosdaq": "KR",
    "tw": "TW",
    "taiwan": "TW",
    "twse": "TW",
    "tpex": "TW",
    "crypto": "CRYPTO",
    "spot": "CRYPTO",
    "futures": "CRYPTO",
    "perpetual": "CRYPTO",
}


def resolve_market(market: str | None) -> str | None:
    """Normalize a market label to a canonical key, or None if unknown.

    Accepts the ``StockAsset.market`` strings Selene already uses ("US"/"CN"/
    "HK"/"CRYPTO", case-insensitive) plus common aliases ("ashare", "kospi").
    """
    if not market:
        return None
    key = market.strip().lower().replace("-", "_")
    if key in _GUIDANCE:
        return key
    return _ALIASES.get(key)


def _lang_key(lang: str | None) -> str:
    return "zh" if (lang or "").strip().lower().startswith("zh") else "en"


def market_guidance(market: str | None, lang: str = "en") -> MarketGuidance | None:
    """Return the guidance record for a market, or None when unrecognized."""
    key = resolve_market(market)
    if key is None:
        return None
    return _GUIDANCE[key][_lang_key(lang)]


def render_market_guidance(market: str | None, lang: str = "en") -> str:
    """Render a market-context prompt block, or "" for an unknown market.

    Fail-open by design: an unrecognized market yields an empty string rather
    than a guess, so a caller can concatenate the result unconditionally
    without injecting a wrong market's narrative.
    """
    key = resolve_market(market)
    if key is None:
        return ""
    lang_key = _lang_key(lang)
    guidance = _GUIDANCE[key][lang_key]
    label = _MARKET_LABEL[key][lang_key]
    if lang_key == "zh":
        heading, subject, focus_label, avoid_label, sep = (
            "## 市场语境",
            "分析标的",
            "关注",
            "不要套用",
            "；",
        )
    else:
        heading, subject, focus_label, avoid_label, sep = (
            "## Market Context",
            "Subject",
            "Focus",
            "Do NOT apply",
            "; ",
        )
    return "\n".join(
        (
            heading,
            f"- {subject}: {label}",
            f"- {focus_label}: {sep.join(guidance.focus)}",
            f"- {avoid_label}: {sep.join(guidance.avoid)}",
        )
    )

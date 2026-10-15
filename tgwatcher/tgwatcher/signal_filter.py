"""
Keyword pre-filter engine for signal processing.

Loads keyword rules from config and performs case-insensitive substring matching
to identify potentially relevant messages for further processing.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Default keyword rules covering crypto/finance news
DEFAULT_KEYWORD_RULES = {
    "bullish": [
        "利好", "上涨", "突破", "新高", "反弹", "买入", "减半",
        "批准", "ETF通过", "通过ETF", "利好消息",
    ],
    "bearish": [
        "利空", "下跌", "暴跌", "崩盘", "黑客", "被盗", "跑路",
        "禁令", "监管", "罚款", "起诉", "利空消息",
    ],
    "event_regulatory": [
        "SEC", "监管", "合规", "禁令", "罚款", "牌照", "起诉", "审查", "法案", "立法",
    ],
    "event_macro": [
        "美联储", "加息", "降息", "CPI", "GDP", "战争", "制裁", "通胀", "利率",
    ],
    "event_exploit": [
        "黑客", "被盗", "漏洞", "攻击", "跑路", "Rug", "闪电贷",
    ],
    "event_whale": [
        "鲸鱼", "巨鲸", "大额转账", "大额转移", "政府抛售", "国家抛售",
        "机构买入", "机构卖出", "MicroStrategy", "持仓", "增持",
        "冷钱包", "热钱包", "钱包异动",
    ],
    "event_listing": [
        "上线", "挂牌", "Binance", "Coinbase", "上架", "Launchpool",
    ],
    "event_partnership": [
        "合作", "集成", "战略", "投资", "收购", "生态",
    ],
    "urgency_high": [
        "紧急", "暴跌", "崩盘", "黑客", "被盗", "SEC起诉", "被黑",
    ],
}


@dataclass
class FilterResult:
    """Result of keyword filtering operation."""

    passed: bool
    matched_keywords: list[str] = field(default_factory=list)
    preliminary_factors: dict = field(default_factory=dict)


class KeywordFilter:
    """
    Keyword-based pre-filter for signal processing.

    Loads keyword rules from config dict and performs case-insensitive
    substring matching. Re-reads rules from config on each call to
    follow existing config mutation patterns.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """
        Initialize the keyword filter.

        Args:
            config: Configuration dict containing 'keywords' and 'min_text_length'.
                   If not provided, uses DEFAULT_KEYWORD_RULES.
        """
        self._config = config or {}
        self._min_text_length = self._config.get("min_text_length", 10)
        logger.debug(
            "KeywordFilter initialized with min_text_length=%d",
            self._min_text_length,
        )

    def filter(self, text: str | None) -> FilterResult:
        """
        Filter text against keyword rules.

        Args:
            text: The text to filter. If None or too short, returns not passed.

        Returns:
            FilterResult with passed status, matched keywords, and preliminary factors.
        """
        # Re-read config on each call (follows existing mutation pattern)
        self._min_text_length = self._config.get("min_text_length", 10)

        # Step 1: Check text validity
        if text is None or len(text) < self._min_text_length:
            logger.debug(
                "Text rejected: length=%s, min_required=%d",
                len(text) if text else 0,
                self._min_text_length,
            )
            return FilterResult(passed=False)

        # Get keyword rules from config or use defaults
        keyword_rules = self._config.get("keywords", DEFAULT_KEYWORD_RULES)

        # Step 2-3: Check each category against text (case-insensitive)
        text_lower = text.lower()
        matched_keywords: list[str] = []
        matched_categories: list[str] = []
        has_high_urgency = False

        for category, keywords in keyword_rules.items():
            category_matches = []
            for keyword in keywords:
                if keyword.lower() in text_lower:
                    category_matches.append(keyword)
                    matched_keywords.append(keyword)

            if category_matches:
                matched_categories.append(category)
                if category == "urgency_high":
                    has_high_urgency = True

        # Step 4: Build result
        passed = len(matched_keywords) > 0
        estimated_urgency = "high" if has_high_urgency else None

        preliminary_factors: dict[str, Any] = {
            "matched_categories": matched_categories,
            "estimated_urgency": estimated_urgency,
        }

        if passed:
            logger.debug(
                "Text passed filter: matched=%d keywords, categories=%s",
                len(matched_keywords),
                matched_categories,
            )

        return FilterResult(
            passed=passed,
            matched_keywords=matched_keywords,
            preliminary_factors=preliminary_factors,
        )

    def update_config(self, config: dict[str, Any]) -> None:
        """
        Update the filter configuration.

        Args:
            config: New configuration dict.
        """
        self._config = config
        logger.debug("KeywordFilter config updated")

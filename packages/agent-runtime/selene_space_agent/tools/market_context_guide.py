"""Market context guide — regime-aware trading cognition on demand.

Pulls the current regime from the state engine plus a market snapshot
(price/funding/OI/events), matches knowledge entries by (regime × intent)
and conditional signals, and renders a compact Chinese guidance text
(≤~500 chars) for the LLM to consume.  Missing data degrades gracefully.
"""

from __future__ import annotations

from typing import Any

from selene_space_types.risk import RiskLevel

from selene_space_agent.tools.base import BaseTool, ToolResult
from selene_space_agent.tools.market_knowledge import (
    CROSS_REGIME_KNOWLEDGE,
    DERIVATIVES_SIGNALS,
    EVENT_RISK_KNOWLEDGE,
    KNOWLEDGE_BASE,
    KnowledgeEntry,
    MarketSnapshot,
)

_MAX_ENTRIES = 8
_KLINE_LIMIT = 50
_DEFAULT_INTERVAL = "1h"

_REGIME_LABELS = {
    "trending": "趋势市",
    "ranging": "震荡市",
    "volatile": "高波动",
}
_INTENT_LABELS = {
    "open_long": "做多",
    "open_short": "做空",
    "close_position": "平仓",
    "stop_loss": "止损",
    "set_risk": "风控设置",
    "general": "综合",
}
_TAG_SECTIONS = {
    "iron-rule": "铁律",
    "risk": "风险",
    "entry": "入场",
    "exit": "离场",
    "event": "事件",
}


def _detect_regime(state_engine: Any, market_service: Any) -> str:
    """Ask the state engine first; fall back to a naive volatility check."""
    getter = getattr(state_engine, "get_regime", None)
    if callable(getter):
        try:
            regime = getter()
            if regime:
                return str(regime)
        except Exception:
            pass

    try:
        klines = market_service.get_recent_klines(
            limit=_KLINE_LIMIT, interval=_DEFAULT_INTERVAL
        )
        closes = [
            float(k["close"]) if isinstance(k, dict) else float(k.close) for k in klines
        ]
    except Exception:
        closes = []

    if len(closes) < 10:
        return "ranging"

    returns = [
        abs(closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1]
    ]
    avg_abs_move = sum(returns) / len(returns)
    net_move = abs(closes[-1] - closes[0]) / closes[0]

    if avg_abs_move > 0.02:
        return "volatile"
    if net_move > 0.05:
        return "trending"
    return "ranging"


def _build_snapshot(state_engine: Any, market_service: Any) -> MarketSnapshot:
    snapshot = MarketSnapshot(regime=_detect_regime(state_engine, market_service))

    try:
        ticker = market_service.get_ticker()
        if ticker is not None:
            snapshot.symbol = getattr(ticker, "symbol", "") or ""
    except Exception:
        pass

    return snapshot


def _match_conditional(
    snapshot: MarketSnapshot, funding_fetcher: Any, event_guard: Any
) -> list[tuple[str, str]]:
    """Return (title, content) pairs whose condition currently holds."""
    matched: list[tuple[str, str]] = []
    funding_rate = snapshot.funding_rate
    if funding_rate is None and funding_fetcher is not None:
        try:
            funding_rate = funding_fetcher.get_current_funding_rate()
        except Exception:
            funding_rate = None
    oi_change = snapshot.open_interest_change

    checks = {
        "funding_extreme_positive": funding_rate is not None and funding_rate > 0.001,
        "funding_extreme_negative": funding_rate is not None and funding_rate < -0.001,
        "oi_surge": oi_change is not None and oi_change > 0.10,
        "oi_dump": oi_change is not None and oi_change < -0.10,
    }
    for name, hit in checks.items():
        entry = DERIVATIVES_SIGNALS.get(name)
        if entry is not None and hit:
            matched.append((entry.title, entry.content))

    has_red = False
    has_orange = False
    if event_guard is not None:
        try:
            alerts = list(event_guard.get_active_alerts(symbol=None, now_ts=None))
        except Exception:
            alerts = []
        for alert in alerts:
            level = str(getattr(alert.level, "value", alert.level)).lower()
            has_red = has_red or level == "red"
            has_orange = has_orange or level == "orange"
    vix_crisis = False
    if event_guard is not None and hasattr(event_guard, "get_vix_regime"):
        try:
            vix_crisis = (
                str(event_guard.get_vix_regime(now_ts=None)).lower() == "crisis"
            )
        except Exception:
            pass

    event_checks = {
        "red_alert_active": has_red,
        "orange_alert_active": has_orange and not has_red,
        "vix_crisis": vix_crisis,
    }
    for entry in EVENT_RISK_KNOWLEDGE:
        if event_checks.get(entry.condition):
            matched.append((entry.title, entry.content))

    return matched


class MarketContextGuideTool(BaseTool):
    def __init__(
        self,
        state_engine: Any,
        market_service: Any,
        funding_fetcher: Any | None = None,
        event_guard: Any | None = None,
    ) -> None:
        self._state_engine = state_engine
        self._market_service = market_service
        self._funding_fetcher = funding_fetcher
        self._event_guard = event_guard

    @property
    def name(self) -> str:
        return "market_context_guide"

    @property
    def description(self) -> str:
        return (
            "Get concise regime-aware trading cognition for the current market "
            "(趋势/震荡/高波动 guidance + derivatives & event risk warnings)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": [
                        "open_long",
                        "open_short",
                        "close_position",
                        "stop_loss",
                        "set_risk",
                        "general",
                    ],
                    "description": "What the agent is about to do (default general)",
                },
            },
            "required": [],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        intent = args.get("intent") or "general"
        if intent not in _INTENT_LABELS:
            intent = "general"

        try:
            snapshot = _build_snapshot(self._state_engine, self._market_service)
        except Exception:
            snapshot = MarketSnapshot(regime="ranging")

        regime = snapshot.regime if snapshot.regime in _REGIME_LABELS else "ranging"

        entries: list[KnowledgeEntry] = []
        exact_key = (regime, intent)
        if exact_key in KNOWLEDGE_BASE:
            entries.extend(KNOWLEDGE_BASE[exact_key])
        elif intent != "general" and (regime, "general") in KNOWLEDGE_BASE:
            entries.extend(KNOWLEDGE_BASE[(regime, "general")])

        conditional = _match_conditional(
            snapshot, self._funding_fetcher, self._event_guard
        )

        lines: list[str] = [
            f"当前市场：{_REGIME_LABELS[regime]}（intent: {_INTENT_LABELS[intent]}）"
        ]
        count = 0

        for entry in CROSS_REGIME_KNOWLEDGE[:2]:
            lines.append(
                f"[{_TAG_SECTIONS.get(entry.tags[0], '认知') if entry.tags else '认知'}] "
                f"{entry.title}：{entry.content}"
            )
            count += 1

        for entry in entries:
            if count >= _MAX_ENTRIES:
                break
            lines.append(f"{entry.title}：{entry.content}")
            count += 1

        for title, content in conditional:
            if count >= _MAX_ENTRIES:
                break
            lines.append(f"[条件触发] {title}：{content}")
            count += 1

        return ToolResult(output="\n".join(lines))

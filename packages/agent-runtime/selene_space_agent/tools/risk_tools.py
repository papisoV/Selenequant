"""Risk management tools — risk check, report, and multi-source pressure.

``RiskCheckTool`` evaluates a trade intent against the risk engine.
``RiskReportTool`` lists configured rules.  ``RiskPressureCheckTool``
aggregates EventGuard alerts + VIX regime into one Chinese-labeled
pressure report with an overall grade and position-size suggestion.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import ValidationError

from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskLevel

from selene_space_agent.tools.base import BaseTool, ToolResult


class RiskCheckTool(BaseTool):
    def __init__(self, risk_engine: Any) -> None:
        self._risk_engine = risk_engine

    @property
    def name(self) -> str:
        return "risk_check"

    @property
    def description(self) -> str:
        return "Check a trade intent against the risk engine before execution."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "intent": {"type": "object", "description": "TradeIntent payload"},
            },
            "required": ["intent"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        intent_raw = args.get("intent")
        if not isinstance(intent_raw, dict):
            return ToolResult(output="", error="Missing required parameter: intent")
        try:
            intent = TradeIntent.model_validate(intent_raw)
        except ValidationError as exc:
            return ToolResult(output="", error=f"Invalid trade intent: {exc}")

        try:
            decision = self._risk_engine.pre_check(intent, context=dict(args))
        except Exception as exc:
            return ToolResult(output="", error=f"Risk check failed: {exc}")

        if decision.allowed:
            return ToolResult(
                output=f"ALLOWED: {decision.reason} (level: {decision.level.value})"
            )
        return ToolResult(
            output=f"BLOCKED: {decision.reason} (level: {decision.level.value})"
        )


class RiskReportTool(BaseTool):
    def __init__(self, risk_engine: Any) -> None:
        self._risk_engine = risk_engine

    @property
    def name(self) -> str:
        return "risk_report"

    @property
    def description(self) -> str:
        return "List all configured risk rules and their enabled state."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        rules = list(getattr(self._risk_engine, "rules", []) or [])
        if not rules:
            return ToolResult(output="No risk rules configured")

        lines = [f"Risk rules ({len(rules)}):"]
        for rule in rules:
            state = "enabled" if rule.enabled else "disabled"
            desc = getattr(rule, "description", "") or ""
            lines.append(f"  [{rule.rule_id}] {state}: {desc}")
        return ToolResult(output="\n".join(lines))


# ---------------------------------------------------------------------------
# RiskPressureCheckTool — macro/VIX/TG aggregation (EventGuard duck-type)
# ---------------------------------------------------------------------------

_SOURCE_LABELS = {
    "macro_calendar": "Macro Calendar",
    "tg_realtime": "TG Realtime",
    "unlock_calendar": "Unlock Calendar",
}

_VIX_MULTIPLIERS = {"high": 0.8, "crisis": 0.5}


def _source_label(source: Any) -> str:
    key = source.value if hasattr(source, "value") else str(source)
    return _SOURCE_LABELS.get(key, key)


def _level_text(level: Any) -> str:
    return level.value if hasattr(level, "value") else str(level)


class RiskPressureCheckTool(BaseTool):
    """Aggregate active alerts and VIX regime into one pressure report."""

    def __init__(self, event_guard: Any) -> None:
        self._guard = event_guard

    @property
    def name(self) -> str:
        return "risk_pressure_check"

    @property
    def description(self) -> str:
        return (
            "Aggregate macro calendar, TG realtime, unlock alerts and VIX regime "
            "into a single risk pressure grade with position sizing advice."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Trading pair to filter alerts, e.g. BTC/USDT",
                },
                "now_ts": {
                    "type": "integer",
                    "description": "Current timestamp in ms (default now)",
                },
            },
            "required": [],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        symbol = args.get("symbol") or "*"
        now_ts = args.get("now_ts") or int(time.time() * 1000)

        try:
            raw_alerts = list(
                self._guard.get_active_alerts(
                    symbol=None if symbol == "*" else symbol, now_ts=now_ts
                )
            )
            vix_regime = str(
                self._guard.get_vix_regime(now_ts=now_ts) or "normal"
            ).lower()
        except Exception as exc:
            return ToolResult(
                output=f"Risk pressure query failed: {exc}",
                error=str(exc),
            )

        # Symbol filter: "*" matches everything; otherwise compare base asset.
        base = symbol.upper().replace("/USDT", "")
        alerts = []
        for alert in raw_alerts:
            alert_symbol = str(alert.symbol or "").upper()
            if alert_symbol == "*" or alert_symbol == base:
                alerts.append(alert)

        lines = [f"Risk pressure report (symbol={symbol})"]

        # -- Alerts section -------------------------------------------------
        if alerts:
            lines.append("== 活跃告警 ==")
            for alert in alerts:
                level = _level_text(alert.level).upper()
                reason = alert.reason or ""
                lines.append(f"[{_source_label(alert.source)}] {level} {reason}")
        else:
            lines.append("== 活跃告警 ==")
            lines.append("无活跃告警 (0 active alerts)")

        # -- VIX section ----------------------------------------------------
        lines.append("== VIX ==")
        lines.append(f"Regime: {vix_regime}")

        # -- Grade ----------------------------------------------------------
        has_red = any(_level_text(a.level).lower() == "red" for a in alerts)
        has_orange = any(
            _level_text(a.level).lower() in ("orange", "high") for a in alerts
        )
        triggered = [
            str(a.event_type).upper() for a in alerts if getattr(a, "event_type", "")
        ]

        if has_red or vix_regime == "crisis":
            grade = "高"
            advice = "不开新仓"
        elif has_orange or vix_regime == "high":
            grade = "中"
            multipliers = [
                float(a.multiplier) for a in alerts if float(a.multiplier) > 0
            ]
            if vix_regime == "high":
                multipliers.append(_VIX_MULTIPLIERS["high"])
            advice = (
                f"仓位系数 {min(multipliers):.2f}"
                if multipliers
                else f"仓位系数 {_VIX_MULTIPLIERS['high']:.2f}"
            )
        elif alerts or vix_regime != "normal":
            grade = "低"
            multipliers = [
                float(a.multiplier) for a in alerts if float(a.multiplier) > 0
            ]
            advice = f"仓位系数 {min(multipliers):.2f}" if multipliers else "正常交易"
        else:
            grade = "无"
            advice = "正常交易"

        lines.append("== 综合 ==")
        lines.append(f"等级: {grade}")
        lines.append(f"建议: {advice}")

        # -- Triggered event types -------------------------------------------
        if triggered:
            lines.append("== Triggered event types ==")
            lines.append(", ".join(dict.fromkeys(triggered)))

        return ToolResult(output="\n".join(lines))

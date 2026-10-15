"""Risk management data models for SeleneQuant."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from selene_space_types.common import Timestamp

#: 示例用固定时间戳(2024-03-21 前后), 只作 OpenAPI 示例, 不参与逻辑。
_EXAMPLE_TS = 1711000000000


def _examples_config(*examples: dict[str, Any]) -> ConfigDict:
    """把若干示例包成 pydantic 的 model_config。"""
    return ConfigDict(json_schema_extra={"examples": list(examples)})


_RULE_EXAMPLE: dict[str, Any] = {
    "id": "rule-max-pos",
    "name": "Max Position Size",
    "description": "Limits single position to 10% of portfolio",
    "level": "restricted",
    "parameters": {"max_position_pct": 0.1},
    "enabled": True,
}

_ALERT_EXAMPLE: dict[str, Any] = {
    "rule_id": "max_position",
    "level": "restricted",
    "message": "Position BTC/USDT would be 25% of equity, max 20%",
    "context": {"symbol": "BTC/USDT", "pct": 0.25},
    "timestamp": _EXAMPLE_TS,
}

_DECISION_EXAMPLE: dict[str, Any] = {
    "allowed": True,
    "level": "restricted",
    "reason": "Position size exceeds limit",
    "alerts": [_ALERT_EXAMPLE],
    "timestamp": _EXAMPLE_TS,
}

_ALERT_CONFIG_EXAMPLE: dict[str, Any] = {
    "enabled": True,
    "min_level": "warning",
    "rules": {
        "kelly_sizing": {"enabled": False},
        "daily_loss_fuse": {"level": "dangerous"},
    },
}

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RiskLevel(str, Enum):
    """Risk severity levels used across SeleneQuant.

    Primary levels (risk alerts):
        INFO / WARNING / RESTRICTED / DANGEROUS / CRITICAL

    Legacy aliases (tool classification, security gateway):
        SAFE = INFO, ADJUSTED = INFO, BLOCKED = CRITICAL

    Only CRITICAL hard-blocks a trade; all others are advisory alerts.
    """

    INFO = "info"
    WARNING = "warning"
    RESTRICTED = "restricted"
    DANGEROUS = "dangerous"
    CRITICAL = "critical"

    # Legacy aliases (tool classification, security gateway)
    SAFE = INFO
    ADJUSTED = INFO
    BLOCKED = CRITICAL

    @classmethod
    def _missing_(cls, value: object) -> "RiskLevel | None":
        """Map legacy string values to their canonical aliases.

        Accepts case-insensitive legacy names ("safe", "adjusted", "blocked")
        and returns the corresponding alias member so old config files and
        tool classifications still parse correctly.

        Caveat: Python 3.12 str,Enum does not invoke _missing_ for
        value-based construction (RiskLevel("safe")) or Pydantic
        deserialization. Use the member attributes (RiskLevel.SAFE) or
        explicit mapping dicts for string→enum conversion.
        """
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        legacy_map = {
            "safe": cls.SAFE,
            "adjusted": cls.ADJUSTED,
            "blocked": cls.BLOCKED,
        }
        return legacy_map.get(normalized)


# ---------------------------------------------------------------------------
# RiskRule
# ---------------------------------------------------------------------------


class RiskRule(BaseModel):
    """Definition of a single risk control rule."""

    id: str = Field(..., description="Unique rule identifier")
    name: str = Field(..., min_length=1, description="Human-readable rule name")
    description: str = Field("", description="What this rule checks")
    level: RiskLevel = Field(
        RiskLevel.WARNING, description="Default severity when triggered"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Rule-specific parameters (e.g. max_position_pct=0.1)",
    )
    enabled: bool = Field(True, description="Whether this rule is active")

    model_config = _examples_config(_RULE_EXAMPLE)


# ---------------------------------------------------------------------------
# RiskDecision
# ---------------------------------------------------------------------------


class RiskDecision(BaseModel):
    """Aggregated result of evaluating all risk rules against a trade intent.

    Only CRITICAL-level alerts set allowed=False (hard block).
    All other levels are advisory — allowed=True with alerts for the UI
    to display as popups/notifications.
    """

    allowed: bool = Field(
        True, description="Whether the action is permitted (False only for CRITICAL)"
    )
    level: RiskLevel = Field(
        RiskLevel.INFO, description="Worst risk level across all triggered rules"
    )
    reason: str = Field("", description="Summary of the worst alert")
    alerts: list[RiskAlert] = Field(
        default_factory=list, description="All triggered risk alerts"
    )
    timestamp: Timestamp = Field(..., description="Decision time (ms epoch)")

    model_config = _examples_config(_DECISION_EXAMPLE)


# ---------------------------------------------------------------------------
# RiskAlert
# ---------------------------------------------------------------------------


class RiskAlert(BaseModel):
    """A single risk alert raised when a rule is triggered.

    Returned by individual rules, collected by RiskEngine into RiskDecision.alerts.
    The UI displays these as popups/notifications in live or paper mode.
    """

    rule_id: str = Field(..., description="Triggering rule ID")
    level: RiskLevel = Field(..., description="Alert severity")
    message: str = Field(
        ..., description="Human-readable alert message (shown in UI popup)"
    )
    context: dict[str, Any] = Field(
        default_factory=dict, description="Additional context (symbol, pct, etc.)"
    )
    timestamp: Timestamp = Field(0, description="Alert time (ms epoch), set by engine")

    model_config = _examples_config(_ALERT_EXAMPLE)


# ---------------------------------------------------------------------------
# RiskAlertConfig — user settings for risk alert behavior
# ---------------------------------------------------------------------------


class RiskAlertConfig(BaseModel):
    """User-configurable risk alert settings.

    Stored in the settings service under key 'risk_alerts'.
    Controls whether alerts appear, minimum severity, and per-rule overrides.
    """

    enabled: bool = Field(True, description="Master switch for risk alert popups")
    min_level: RiskLevel = Field(
        RiskLevel.INFO,
        description="Minimum severity to show (INFO shows all, WARNING hides INFO, etc.)",
    )
    rules: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-rule overrides: {rule_id: {enabled: bool, level: str}}",
    )

    model_config = _examples_config(_ALERT_CONFIG_EXAMPLE)

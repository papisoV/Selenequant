"""Improvement suggestions derived from detected issues, and their application."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from selene_space_strategy.models import EngineStrategyConfig


@dataclass(frozen=True)
class StrategyImprovement:
    """One concrete change proposal for a strategy configuration."""

    description: str
    category: str
    suggested_changes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeedbackReport:
    """Issues found plus the suggestions addressing them."""

    issues: list[str] = field(default_factory=list)
    suggestions: list[StrategyImprovement] = field(default_factory=list)


class SuggestionGenerator:
    """Map each issue category to a concrete, applicable change."""

    def for_issues(self, issues: list[str]) -> list[StrategyImprovement]:
        suggestions: list[StrategyImprovement] = []
        for issue in issues:
            suggestions.extend(self._templates_for(issue))
        return suggestions

    def _templates_for(self, issue: str) -> list[StrategyImprovement]:
        if issue == "sharpe_negative":
            return [
                StrategyImprovement(
                    description="Retune entry/exit parameters to recover risk-adjusted returns.",
                    category="parameter_tuning",
                    suggested_changes={},
                ),
                StrategyImprovement(
                    description="Tighten stop loss to cut tail losses.",
                    category="risk",
                    suggested_changes={"risk_params": {"stop_loss_pct": 0.02}},
                ),
            ]
        if issue == "excessive_drawdown":
            return [
                StrategyImprovement(
                    description="Reduce per-trade position size to cap drawdown.",
                    category="risk",
                    suggested_changes={"risk_params": {"max_position_pct": 0.05}},
                ),
                StrategyImprovement(
                    description="Add a trend filter to skip adverse regimes.",
                    category="add_filter",
                    suggested_changes={},
                ),
            ]
        if issue == "low_win_rate":
            return [
                StrategyImprovement(
                    description="Add a confirmation filter (e.g. volume or ADX) before entries.",
                    category="add_filter",
                    suggested_changes={},
                ),
            ]
        if issue == "too_few_trades":
            return [
                StrategyImprovement(
                    description="Drop to a faster timeframe to increase trade frequency.",
                    category="timeframe",
                    suggested_changes={"interval": "15m"},
                ),
            ]
        if issue == "too_many_trades":
            return [
                StrategyImprovement(
                    description="Add a minimum-signal-strength filter to cut noise trades.",
                    category="add_filter",
                    suggested_changes={},
                ),
            ]
        return []


def apply_suggestion(
    config: EngineStrategyConfig,
    suggestion: StrategyImprovement,
) -> EngineStrategyConfig:
    """Return a new config with the suggestion applied (original untouched).

    Recognized shapes inside ``suggested_changes``:
      - ``{"interval": "4h"}``            → top-level field update
      - ``{"parameters": {...}}``         → merged into ``config.parameters``
      - ``{"risk_params": {...}}``        → merged into ``config.risk_params``
    """
    updates: dict[str, Any] = {}
    parameters = dict(config.parameters)
    risk_params = dict(config.risk_params)

    for key, value in suggestion.suggested_changes.items():
        if key == "parameters":
            parameters.update(value)
        elif key == "risk_params":
            risk_params.update(value)
        else:
            updates[key] = value

    if parameters != config.parameters:
        updates["parameters"] = parameters
    if risk_params != config.risk_params:
        updates["risk_params"] = risk_params

    return config.model_copy(update=updates) if updates else config

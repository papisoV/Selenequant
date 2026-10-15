"""Backtest feedback — manual analysis and improvement suggestions (v0.1)."""

from __future__ import annotations

from selene_space_agent.feedback.analyzer import ResultAnalyzer
from selene_space_agent.feedback.loop import BacktestFeedbackLoop
from selene_space_agent.feedback.suggestions import (
    FeedbackReport,
    StrategyImprovement,
    SuggestionGenerator,
    apply_suggestion,
)

__all__ = [
    "BacktestFeedbackLoop",
    "FeedbackReport",
    "ResultAnalyzer",
    "StrategyImprovement",
    "SuggestionGenerator",
    "apply_suggestion",
]

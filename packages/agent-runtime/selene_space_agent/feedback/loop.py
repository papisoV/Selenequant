"""Backtest feedback loop — analyze a result and propose improvements."""

from __future__ import annotations

from selene_space_agent.feedback.analyzer import ResultAnalyzer
from selene_space_agent.feedback.suggestions import (
    FeedbackReport,
    SuggestionGenerator,
)
from selene_space_types.strategy import BacktestResult


class BacktestFeedbackLoop:
    """One-shot analysis: metrics in, issues + suggestions out."""

    def __init__(self) -> None:
        self._analyzer = ResultAnalyzer()
        self._generator = SuggestionGenerator()

    def analyze(self, result: BacktestResult) -> FeedbackReport:
        metrics = result.metrics
        total_trades = getattr(result, "trades_count", None)
        if total_trades is None:
            total_trades = getattr(metrics, "total_trades", 0)

        issues = self._analyzer.detect_issues(
            metrics.model_dump() if hasattr(metrics, "model_dump") else dict(metrics),
            int(total_trades or 0),
        )
        suggestions = self._generator.for_issues(issues)
        return FeedbackReport(issues=issues, suggestions=suggestions)

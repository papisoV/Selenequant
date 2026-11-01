"""Issue detection over backtest results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ISSUE_SHARPE_NEGATIVE = "sharpe_negative"
ISSUE_EXCESSIVE_DRAWDOWN = "excessive_drawdown"
ISSUE_LOW_WIN_RATE = "low_win_rate"
ISSUE_TOO_FEW_TRADES = "too_few_trades"
ISSUE_TOO_MANY_TRADES = "too_many_trades"

_SHARPE_FLOOR = 0.0
_DRAWDOWN_LIMIT = -0.30
_WIN_RATE_FLOOR = 0.35
_TRADES_LOWER = 10
_TRADES_UPPER = 500


@dataclass
class AnalysisResult:
    """Raw issue list produced by a single analysis pass."""

    issues: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class ResultAnalyzer:
    """Threshold rules mapping backtest metrics to issue identifiers."""

    def detect_issues(
        self, metrics: dict[str, Any], total_trades: int | None = None
    ) -> list[str]:
        issues: list[str] = []

        sharpe = self._num(metrics.get("sharpe_ratio"))
        if sharpe is not None and sharpe < _SHARPE_FLOOR:
            issues.append(ISSUE_SHARPE_NEGATIVE)

        drawdown = self._num(metrics.get("max_drawdown"))
        if drawdown is not None and drawdown <= _DRAWDOWN_LIMIT:
            issues.append(ISSUE_EXCESSIVE_DRAWDOWN)

        win_rate = self._num(metrics.get("win_rate"))
        if win_rate is not None and win_rate < _WIN_RATE_FLOOR:
            issues.append(ISSUE_LOW_WIN_RATE)

        if total_trades is None:
            total_trades = int(self._num(metrics.get("total_trades")) or 0)
        if total_trades < _TRADES_LOWER:
            issues.append(ISSUE_TOO_FEW_TRADES)
        elif total_trades > _TRADES_UPPER:
            issues.append(ISSUE_TOO_MANY_TRADES)

        return issues

    @staticmethod
    def _num(value: Any) -> float | None:
        return float(value) if isinstance(value, (int, float)) else None

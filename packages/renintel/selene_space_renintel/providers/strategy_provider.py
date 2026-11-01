"""Strategy provider — 6 Renaissance quant strategies.

Strategies: mean_reversion / momentum / statistical_arbitrage /
execution / risk_management / analysis. *`,
fallback to static descriptions.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from selene_space_renintel.base import (
    RenIntelKind,
    RenIntelProviderConfig,
    RenIntelReport,
)

logger = logging.getLogger(__name__)


FALLBACK_STRATEGIES: dict[str, dict[str, Any]] = {
    "mean_reversion": {
        "name": "Mean Reversion Strategy",
        "description": "Identify assets deviating from fair value, trade convergence",
        "parameters": ["lookback", "z_score_threshold", "exit_z"],
    },
    "momentum": {
        "name": "Momentum Strategy",
        "description": "Trade continuation of price trends with volume confirmation",
        "parameters": ["trend_window", "volume_threshold", "exit_window"],
    },
    "statistical_arbitrage": {
        "name": "Statistical Arbitrage",
        "description": "Cointegration-based pair trading with mean reversion",
        "parameters": ["cointegration_window", "entry_z", "exit_z"],
    },
    "execution": {
        "name": "Execution Strategy",
        "description": "Optimal order execution with market impact minimization",
        "parameters": ["participation_rate", "time_horizon"],
    },
    "risk_management": {
        "name": "Risk Management Strategy",
        "description": "Portfolio-level risk limits and stress testing",
        "parameters": ["var_limit", "max_correlation", "sector_cap"],
    },
    "analysis": {
        "name": "Analysis Strategy",
        "description": "Cross-sectional factor analysis and ranking",
        "parameters": ["factors", "universe_size", "rebalance_freq"],
    },
}


def _load_strategies() -> Any:
    """Return static fallback_strategies data."""
    return FALLBACK_STRATEGIES


class StrategyProvider:
    """Async strategy library provider — 6 Renaissance quant strategies."""

    def __init__(self, cfg: RenIntelProviderConfig) -> None:
        self._cfg = cfg
        self._running = False

    @property
    def config(self) -> RenIntelProviderConfig:
        return self._cfg

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def healthcheck(self) -> bool:
        return True

    async def compute(
        self,
        *,
        action: str,
        task: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> RenIntelReport:
        if action not in {"list_strategies", "get_strategy", "info"}:
            raise KeyError(f"Unknown action: {action}")
        if action == "info":
            return RenIntelReport(
                provider=self._cfg.provider,
                kind=RenIntelKind.OTHER,
                title="Strategy Library Info",
                metrics={
                    "strategy_count": len(_load_strategies()),
                },
                computed_at=datetime.now(UTC),
            )

        strategies = _load_strategies()
        if action == "list_strategies":
            return RenIntelReport(
                provider=self._cfg.provider,
                kind=RenIntelKind.STRATEGY,
                title="Renaissance Strategies",
                metrics={"count": len(strategies)},
                artifacts={"strategies": strategies},
                computed_at=datetime.now(UTC),
            )

        # get_strategy
        params = params or {}
        sid = str(params.get("id", "mean_reversion"))
        if sid not in strategies:
            raise ValueError(f"Strategy not found: {sid}. Available: {list(strategies)}")
        return RenIntelReport(
            provider=self._cfg.provider,
            kind=RenIntelKind.STRATEGY,
            title=f"Strategy: {strategies[sid].get('name', sid)}",
            metrics={"id": sid},
            artifacts={"strategy": strategies[sid]},
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["list_strategies", "get_strategy", "info"]

    def describe_action(self, action: str) -> str:
        return {
            "list_strategies": "List all 6 Renaissance strategies",
            "get_strategy": "Get a strategy by id (param: id=mean_reversion)",
            "info": "Provider info",
        }.get(action, action)

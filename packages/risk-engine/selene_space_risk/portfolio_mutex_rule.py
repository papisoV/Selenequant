"""PortfolioMutexRule — reject concurrent cross-strategy opens on one symbol.

The rule answers one question: does another strategy already hold an unexpired
lock on the intent's symbol? If so, the intent is RESTRICTED (advisory, not a
hard block — the live engine is expected to skip opening). If the symbol is
free, the rule does not acquire here; acquisition is the caller's job after
the order actually fills (acquire-on-fill), keeping the lock table free of
locks that were never real positions.

``strategy_id`` is read from context (injected by the signal driver). When it
is absent the rule has no actor to reason about and silently allows, logging
the miss for operators — a mutex must never hard-fail unknown callers.
"""

from __future__ import annotations

import logging
from typing import Any

from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel

from selene_space_risk.portfolio_mutex import PortfolioMutex

logger = logging.getLogger(__name__)


class PortfolioMutexRule:
    """Reject a second strategy opening the same symbol as a live lock holder.

    Context keys:
        strategy_id (str): ID of the strategy submitting the intent.
        portfolio_mutex (PortfolioMutex): the shared mutex instance (optional;
            when absent the rule falls back to ``self.mutex``).
    """

    def __init__(
        self,
        mutex: PortfolioMutex,
        *,
        enabled: bool = True,
    ) -> None:
        self._mutex = mutex
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "portfolio_mutex"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        if not self._enabled:
            return None

        strategy_id = context.get("strategy_id")
        if not isinstance(strategy_id, str) or not strategy_id:
            logger.debug(
                "portfolio_mutex: no strategy_id in context, allowing %s", intent.symbol
            )
            return None

        mutex = context.get("portfolio_mutex")
        if not isinstance(mutex, PortfolioMutex):
            mutex = self._mutex

        owner = mutex.owner_of(intent.symbol)
        if owner is None:
            return None
        if owner == strategy_id:
            return None

        return RiskAlert(
            rule_id=self.rule_id,
            level=RiskLevel.RESTRICTED,
            message=(
                f"symbol {intent.symbol} 已被策略 {owner} 持有，"
                f"拒绝策略 {strategy_id} 并发开仓"
            ),
            context={
                "symbol": intent.symbol,
                "owner_strategy": owner,
                "strategy_id": strategy_id,
            },
            timestamp=0,  # engine overwrites with its own clock
        )

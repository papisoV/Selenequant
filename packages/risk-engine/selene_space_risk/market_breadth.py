"""Market breadth filter — whole-market advance/decline filter (A6).

Market breadth measures how many symbols are advancing vs declining at a
given point. When more symbols fall than rise (negative breadth), the tape
is weak and long entries carry a poor win-rate. This module:

  1. Computes the advance/decline difference across many symbols over a
     trailing ``window`` (breadth = advancers - decliners).
  2. The rule alerts when breadth falls below ``min_breadth``, filtering
     long entries in weak markets (the A/B strategies are long-oriented).
"""

from __future__ import annotations

from typing import Any

from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel
from selene_space_types.trading import OrderSide


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def _alert(rule_id: str, level: RiskLevel, message: str, **context: Any) -> RiskAlert:
    return RiskAlert(
        rule_id=rule_id,
        level=level,
        message=message,
        context=context,
        timestamp=_now_ms(),
    )


def market_breadth_counts(prices: dict[str, list[float]], window: int = 1) -> tuple[int, int]:
    """Count advancers/decliners across symbols over the trailing ``window`` bars.

    A symbol whose series is too short to span the window (``len <= window``)
    is excluded from the count. A zero net change counts as neither side.
    """
    advancers = 0
    decliners = 0
    for series in prices.values():
        if len(series) <= window:  # need window+1 points to span the window
            continue
        change = series[-1] - series[-1 - window]
        if change > 0:
            advancers += 1
        elif change < 0:
            decliners += 1
    return advancers, decliners


def compute_market_breadth(prices: dict[str, list[float]], window: int = 1) -> int:
    """Whole-market breadth: advancers minus decliners across all symbols."""
    advancers, decliners = market_breadth_counts(prices, window)
    return advancers - decliners


class MarketBreadthFilter:
    """Filters long entries when whole-market breadth is too weak.

    A positive ``min_breadth`` requires more advancers than decliners to let
    a long through; the default 0 requires at least a non-negative tape.

    Context keys:
        price_history (dict[str, list[float]]): symbol → close-price series.
            Breadth is computed over the most recent ``window`` bars.
    """

    def __init__(
        self,
        min_breadth: int = 0,
        window: int = 1,
        *,
        enabled: bool = True,
    ) -> None:
        """Initialize market breadth filter.

        Args:
            min_breadth: Minimum acceptable breadth (advancers - decliners).
                Breadth below this filters the trade.
            window: Trailing bars over which each symbol's move is measured.
            enabled: Whether the rule is active.
        """
        if window <= 0:
            raise ValueError("window must be positive")
        self._min_breadth = min_breadth
        self._window = window
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "market_breadth"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        if not self._enabled:
            return None
        if intent.side != OrderSide.BUY:
            return None  # weak tape only filters long entries

        prices = context.get("price_history", {})
        breadth = compute_market_breadth(prices, self._window)
        if breadth < self._min_breadth:
            advancers, decliners = market_breadth_counts(prices, self._window)
            return _alert(
                self.rule_id, RiskLevel.RESTRICTED,
                f"全市场涨跌家数差 {breadth:+d}（涨 {advancers} / 跌 {decliners}），低于门槛 {self._min_breadth:+d}，市况偏弱",
                symbol=intent.symbol,
                breadth=breadth,
                advancers=advancers,
                decliners=decliners,
                min_breadth=self._min_breadth,
                window=self._window,
            )
        return None

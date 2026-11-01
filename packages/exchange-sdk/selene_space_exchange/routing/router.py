"""Smart Order Router -- select the optimal exchange for order execution.

Evaluates quotes from multiple exchanges and routes the order to the one
that minimizes total cost, fees, slippage, or latency depending on the
chosen strategy.  Also supports liquidity-weighted order splitting for
large orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class QuoteProvider(Protocol):
    """Async callable that fetches per-exchange quotes for a routing decision.

    Implementations typically call each venue's REST ticker / order-book
    endpoint and return a list of ``ExchangeInfo`` (one per venue). The
    router then selects the best venue according to its strategy.

    Async because real quote providers fan out REST calls to multiple
    exchanges — a synchronous signature would force callers to block the
    event loop or hide async work behind a sync wrapper, both of which
    break in production. LiveExecutionEngine awaits this when
    ``place_order(metadata={"route": True})`` is requested and a router
    is wired in.
    """

    async def __call__(
        self,
        symbol: str,
        side: str,
        quantity: float,
    ) -> list[ExchangeInfo]:
        ...


@dataclass(frozen=True)
class ExchangeInfo:
    """Quote metadata for a single exchange.

    Attributes:
        exchange_id: Unique identifier for the exchange (e.g. "binance").
        fee_rate: Trading fee as a decimal fraction (e.g. 0.001 = 0.1%).
        estimated_slippage_bps: Estimated slippage in basis points.
        available_liquidity: Notional liquidity available at the quoted level.
        latency_ms: Round-trip latency in milliseconds.
    """

    exchange_id: str
    fee_rate: float
    estimated_slippage_bps: float
    available_liquidity: float
    latency_ms: int


@dataclass(frozen=True)
class RoutingDecision:
    """Result of the routing decision.

    Attributes:
        selected_exchange: The exchange chosen for execution.
        all_quotes: Full list of quotes that were evaluated.
        reason: Human-readable explanation for the selection.
    """

    selected_exchange: str
    all_quotes: list[ExchangeInfo] = field(default_factory=list)
    reason: str = ""


class SmartOrderRouter:
    """Route orders to the best exchange based on a configurable strategy.

    Supported strategies:
        - ``"best_price"``: Minimize fee + slippage (total cost).
        - ``"lowest_fee"``: Minimize fee rate.
        - ``"best_liquidity"``: Maximize available liquidity.
        - ``"lowest_latency"``: Minimize round-trip latency.

    Args:
        strategy: Routing strategy name.  Defaults to ``"best_price"``.

    Raises:
        ValueError: If *strategy* is not one of the supported values.
    """

    _STRATEGIES = frozenset({"best_price", "lowest_fee", "best_liquidity", "lowest_latency"})

    def __init__(self, strategy: str = "best_price") -> None:
        if strategy not in self._STRATEGIES:
            raise ValueError(
                f"Unknown strategy '{strategy}'. "
                f"Supported: {sorted(self._STRATEGIES)}"
            )
        self._strategy = strategy

    @property
    def strategy(self) -> str:
        """Return the active routing strategy name."""
        return self._strategy

    # ------------------------------------------------------------------
    # Core routing
    # ------------------------------------------------------------------

    def route_order(self, quotes: list[ExchangeInfo]) -> RoutingDecision:
        """Select the best exchange from *quotes* according to the strategy.

        Args:
            quotes: One or more exchange quotes to evaluate.

        Returns:
            A ``RoutingDecision`` with the selected exchange and reason.

        Raises:
            ValueError: If *quotes* is empty.
        """
        if not quotes:
            raise ValueError("Cannot route order: quotes list is empty")

        if self._strategy == "best_price":
            return self._route_best_price(quotes)
        if self._strategy == "lowest_fee":
            return self._route_lowest_fee(quotes)
        if self._strategy == "best_liquidity":
            return self._route_best_liquidity(quotes)
        # lowest_latency
        return self._route_lowest_latency(quotes)

    # ------------------------------------------------------------------
    # Comparison helpers
    # ------------------------------------------------------------------

    def compare_fees(
        self, quotes: list[ExchangeInfo], order_notional: float
    ) -> list[tuple[str, float]]:
        """Compare total fees for each exchange.

        Args:
            quotes: Exchange quotes to compare.
            order_notional: Order size in notional terms (e.g. USD).

        Returns:
            List of ``(exchange_id, total_fee)`` tuples sorted ascending
            by total fee.
        """
        results = [
            (q.exchange_id, q.fee_rate * order_notional) for q in quotes
        ]
        return sorted(results, key=lambda x: x[1])

    def compare_total_cost(
        self, quotes: list[ExchangeInfo], order_notional: float
    ) -> list[tuple[str, float]]:
        """Compare total cost (fee + slippage) for each exchange.

        Total cost = fee_rate * order_notional
                   + (estimated_slippage_bps / 10_000) * order_notional

        Args:
            quotes: Exchange quotes to compare.
            order_notional: Order size in notional terms.

        Returns:
            List of ``(exchange_id, total_cost)`` tuples sorted ascending
            by total cost.
        """
        results = [
            (
                q.exchange_id,
                q.fee_rate * order_notional
                + (q.estimated_slippage_bps / 10_000) * order_notional,
            )
            for q in quotes
        ]
        return sorted(results, key=lambda x: x[1])

    def compare_slippage(
        self, quotes: list[ExchangeInfo],
    ) -> list[tuple[str, float]]:
        """Compare estimated slippage across exchanges.

        Args:
            quotes: Exchange quotes to compare.

        Returns:
            List of ``(exchange_id, slippage_bps)`` tuples sorted
            ascending by slippage.
        """
        results = [
            (q.exchange_id, q.estimated_slippage_bps) for q in quotes
        ]
        return sorted(results, key=lambda x: x[1])

    # ------------------------------------------------------------------
    # Order splitting
    # ------------------------------------------------------------------

    def split_order(
        self,
        quotes: list[ExchangeInfo],
        total_qty: float,
        min_split_pct: float = 0.1,
    ) -> list[tuple[str, float]]:
        """Split a large order across exchanges by liquidity-weighted allocation.

        Each exchange receives a share proportional to its available
        liquidity.  No exchange receives less than ``min_split_pct`` of
        *total_qty*.  If an exchange's proportional share is below the
        minimum, it is raised to the minimum and the remainder is
        redistributed from other exchanges.

        Args:
            quotes: Exchange quotes with liquidity information.
            total_qty: Total quantity to split.
            min_split_pct: Minimum fraction of *total_qty* per exchange.

        Returns:
            List of ``(exchange_id, allocated_qty)`` tuples.

        Raises:
            ValueError: If *quotes* is empty or *total_qty* is not positive.
        """
        if not quotes:
            raise ValueError("Cannot split order: quotes list is empty")
        if total_qty <= 0:
            raise ValueError("total_qty must be positive")

        n = len(quotes)
        min_qty = total_qty * min_split_pct

        total_liquidity = sum(q.available_liquidity for q in quotes)

        # Edge case: zero total liquidity -- distribute equally
        if total_liquidity == 0:
            equal_share = total_qty / n
            return [(q.exchange_id, equal_share) for q in quotes]

        # Initial proportional allocation
        raw_allocations: list[float] = []
        for q in quotes:
            share = (q.available_liquidity / total_liquidity) * total_qty
            raw_allocations.append(share)

        # Enforce minimum allocation
        below_min = [i for i, a in enumerate(raw_allocations) if a < min_qty]
        above_min = [i for i, a in enumerate(raw_allocations) if a >= min_qty]

        if below_min:
            # Raise under-allocated exchanges to the minimum
            deficit = sum(min_qty - raw_allocations[i] for i in below_min)
            for i in below_min:
                raw_allocations[i] = min_qty

            # Redistribute deficit proportionally from above-min exchanges
            above_liquidity = sum(
                quotes[i].available_liquidity for i in above_min
            )
            for i in above_min:
                if above_liquidity > 0:
                    reduction = (
                        (quotes[i].available_liquidity / above_liquidity)
                        * deficit
                    )
                    raw_allocations[i] = max(
                        min_qty, raw_allocations[i] - reduction
                    )

        return [(quotes[i].exchange_id, raw_allocations[i]) for i in range(n)]

    # ------------------------------------------------------------------
    # Private strategy implementations
    # ------------------------------------------------------------------

    def _route_best_price(self, quotes: list[ExchangeInfo]) -> RoutingDecision:
        """Select exchange with lowest total cost (fee + slippage)."""
        best = min(quotes, key=lambda q: q.fee_rate + q.estimated_slippage_bps / 10_000)
        return RoutingDecision(
            selected_exchange=best.exchange_id,
            all_quotes=quotes,
            reason="lowest total cost",
        )

    def _route_lowest_fee(self, quotes: list[ExchangeInfo]) -> RoutingDecision:
        """Select exchange with the lowest fee rate."""
        best = min(quotes, key=lambda q: q.fee_rate)
        return RoutingDecision(
            selected_exchange=best.exchange_id,
            all_quotes=quotes,
            reason="lowest fee rate",
        )

    def _route_best_liquidity(self, quotes: list[ExchangeInfo]) -> RoutingDecision:
        """Select exchange with the highest available liquidity."""
        best = max(quotes, key=lambda q: q.available_liquidity)
        return RoutingDecision(
            selected_exchange=best.exchange_id,
            all_quotes=quotes,
            reason="best liquidity",
        )

    def _route_lowest_latency(self, quotes: list[ExchangeInfo]) -> RoutingDecision:
        """Select exchange with the lowest latency."""
        best = min(quotes, key=lambda q: q.latency_ms)
        return RoutingDecision(
            selected_exchange=best.exchange_id,
            all_quotes=quotes,
            reason="lowest latency",
        )

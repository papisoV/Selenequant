"""Rebalance executor — converts target weights to executable orders.

Wraps selene_space_alpha.portfolio.rebalance.compute_rebalance_orders
and provides a clean bridge between alpha-core optimizers and the
execution layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from selene_space_alpha.portfolio.rebalance import compute_rebalance_orders


@dataclass(frozen=True)
class ExecutableOrder:
    symbol: str
    side: str
    quantity: float
    price: float
    weight_drift: float


class RebalanceExecutor:
    """Converts target weights into executable orders.

    Args:
        min_drift: Minimum weight drift to generate an order (0.01 = 1%).
    """

    def __init__(self, min_drift: float = 0.01) -> None:
        self._min_drift = min_drift

    def compute_orders(
        self,
        target_weights: dict[str, float],
        current_positions: dict[str, float],
        prices: dict[str, float],
        total_equity: float,
    ) -> list[ExecutableOrder]:
        """Compute rebalance orders from target weights.

        Args:
            target_weights: Symbol → target weight.
            current_positions: Symbol → current position value (quote).
            prices: Symbol → current price.
            total_equity: Total portfolio equity.

        Returns:
            List of ExecutableOrders.
        """
        orders = compute_rebalance_orders(
            target_weights=target_weights,
            current_positions=current_positions,
            prices=prices,
            total_equity=total_equity,
            min_drift=self._min_drift,
        )

        return [
            ExecutableOrder(
                symbol=o.symbol,
                side=o.side,
                quantity=round(o.quantity, 8),
                price=o.price,
                weight_drift=round(o.weight_drift, 6),
            )
            for o in orders
        ]

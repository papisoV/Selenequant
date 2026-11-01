"""Portfolio rebalance engine — bridge from optimizer output to executable orders.

Takes an OptResult (target weights) and current portfolio state, then
generates a list of rebalance orders to move from current to target
allocation.

Usage:
    from selene_space_alpha.portfolio.rebalance import compute_rebalance_orders, RebalanceOrder

    orders = compute_rebalance_orders(
        target_weights={"BTC/USDT": 0.5, "ETH/USDT": 0.3, "SOL/USDT": 0.2},
        current_positions={"BTC/USDT": 1000, "ETH/USDT": 500, "SOL/USDT": 0},
        prices={"BTC/USDT": 50000, "ETH/USDT": 3000, "SOL/USDT": 150},
        total_equity=10000,
    )
    for order in orders:
        print(f"{order.side} {order.symbol}: {order.quantity} @ {order.price}")
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RebalanceOrder:
    """A single rebalance order.

    Attributes:
        symbol: Trading pair.
        side: "buy" or "sell".
        quantity: Number of units to trade.
        price: Reference price for the order.
        current_weight: Current portfolio weight for this symbol.
        target_weight: Target portfolio weight for this symbol.
        weight_drift: Absolute difference |target - current|.
    """

    symbol: str
    side: str
    quantity: float
    price: float
    current_weight: float
    target_weight: float
    weight_drift: float


def compute_rebalance_orders(
    target_weights: dict[str, float],
    current_positions: dict[str, float],
    prices: dict[str, float],
    total_equity: float,
    min_drift: float = 0.01,
) -> list[RebalanceOrder]:
    """Compute rebalance orders to move from current to target allocation.

    Args:
        target_weights: Symbol → target weight (should sum to ~1.0).
        current_positions: Symbol → current position value in quote currency.
        prices: Symbol → current price per unit.
        total_equity: Total portfolio equity in quote currency.
        min_drift: Minimum weight drift to trigger an order (default 1%).
            Prevents churn from negligible rebalancing.

    Returns:
        List of RebalanceOrders sorted by weight_drift (largest first).
    """
    if total_equity <= 0:
        return []

    all_symbols = set(target_weights) | set(current_positions)

    current_weights: dict[str, float] = {}
    for sym in all_symbols:
        pos_value = current_positions.get(sym, 0.0)
        current_weights[sym] = pos_value / total_equity

    orders: list[RebalanceOrder] = []
    for sym in all_symbols:
        tw = target_weights.get(sym, 0.0)
        cw = current_weights.get(sym, 0.0)
        drift = tw - cw

        if abs(drift) < min_drift:
            continue

        price = prices.get(sym, 0.0)
        if price <= 0:
            continue

        # Dollar amount to trade
        dollar_delta = drift * total_equity
        quantity = abs(dollar_delta) / price

        if quantity <= 0:
            continue

        side = "buy" if drift > 0 else "sell"

        orders.append(RebalanceOrder(
            symbol=sym,
            side=side,
            quantity=quantity,
            price=price,
            current_weight=cw,
            target_weight=tw,
            weight_drift=abs(drift),
        ))

    # Sort by drift (largest deviation first)
    orders.sort(key=lambda o: o.weight_drift, reverse=True)
    return orders


def compute_rebalance_from_optresult(
    opt_result: object,
    symbols: list[str],
    current_positions: dict[str, float],
    prices: dict[str, float],
    total_equity: float,
    min_drift: float = 0.01,
) -> list[RebalanceOrder]:
    """Compute rebalance orders from an optimizer result.

    Bridges the gap between optimizers.py (OptResult) and executable orders.

    Args:
        opt_result: OptResult with .weights (numpy array) and .success.
        symbols: Symbol names in the same order as the weight vector.
        current_positions: Symbol → current position value.
        prices: Symbol → current price.
        total_equity: Total portfolio equity.
        min_drift: Minimum weight drift to trigger order.

    Returns:
        List of RebalanceOrders. Empty if optimization failed.
    """
    if not getattr(opt_result, "success", False):
        return []

    weights = np.asarray(opt_result.weights)
    if len(weights) != len(symbols):
        raise ValueError(
            f"weights length ({len(weights)}) != symbols length ({len(symbols)})"
        )

    target_weights = dict(zip(symbols, weights))
    return compute_rebalance_orders(
        target_weights=target_weights,
        current_positions=current_positions,
        prices=prices,
        total_equity=total_equity,
        min_drift=min_drift,
    )


def estimate_turnover(
    target_weights: dict[str, float],
    current_positions: dict[str, float],
    total_equity: float,
) -> float:
    """Estimate portfolio turnover from a rebalance.

    Turnover = sum of absolute weight changes / 2.
    A turnover of 0.1 means 10% of the portfolio needs to be traded.

    Args:
        target_weights: Symbol → target weight.
        current_positions: Symbol → current position value.
        total_equity: Total portfolio equity.

    Returns:
        Estimated turnover as a fraction of total equity.
    """
    if total_equity <= 0:
        return 0.0

    all_symbols = set(target_weights) | set(current_positions)
    total_drift = 0.0
    for sym in all_symbols:
        tw = target_weights.get(sym, 0.0)
        cw = current_positions.get(sym, 0.0) / total_equity
        total_drift += abs(tw - cw)

    # Divide by 2: each trade has a buy and sell side
    return total_drift / 2.0

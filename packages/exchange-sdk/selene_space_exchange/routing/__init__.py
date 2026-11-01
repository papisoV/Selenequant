"""Smart Order Routing -- multi-exchange order execution optimization.

Provides routing strategies that select the best exchange for order
execution based on fees, slippage, liquidity, and latency.
"""

from selene_space_exchange.routing.router import (
    ExchangeInfo,
    QuoteProvider,
    RoutingDecision,
    SmartOrderRouter,
)

__all__ = [
    "ExchangeInfo",
    "QuoteProvider",
    "RoutingDecision",
    "SmartOrderRouter",
]

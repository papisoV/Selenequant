"""Market making strategies for SeleneQuant.

Currently implements the Avellaneda-Stoikov (2008) optimal market making model
with inventory management and quote skewing.
"""

from selene_space_strategy.market_making.as_model import (
    ASParams,
    ASQuotes,
    as_optimal_depths,
    as_optimal_spread,
    as_quotes,
    as_quotes_with_params,
    as_reservation_price,
)
from selene_space_strategy.market_making.inventory import (
    InventoryManager,
    InventoryState,
    adjust_sizes,
    skew_quotes,
)

__all__ = [
    # AS Model
    "ASParams",
    "ASQuotes",
    "as_optimal_depths",
    "as_optimal_spread",
    "as_quotes",
    "as_quotes_with_params",
    "as_reservation_price",
    # Inventory
    "InventoryManager",
    "InventoryState",
    "adjust_sizes",
    "skew_quotes",
]

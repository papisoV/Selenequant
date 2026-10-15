"""Inventory management for market making strategies.

Provides inventory tracking, quote skewing, and size adjustment to keep
the market maker's inventory within acceptable bounds.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from selene_space_strategy.market_making.as_model import ASQuotes


@dataclass
class InventoryState:
    """Snapshot of the market maker's inventory state.

    Attributes:
        current_inventory: Net inventory (positive = long, negative = short).
        realized_pnl: Cumulative realized profit and loss.
        trade_count: Number of fills executed.
        max_inventory: Maximum allowed absolute inventory.
    """

    current_inventory: float
    realized_pnl: float
    trade_count: int
    max_inventory: float


class InventoryManager:
    """Manages inventory state and enforces position limits.

    Tracks fills, computes PnL, and checks whether a new fill would
    exceed the maximum inventory constraint.

    Args:
        max_inventory: Maximum absolute inventory allowed.
        target_inventory: Desired inventory level (default 0 = flat).
    """

    def __init__(
        self,
        max_inventory: float,
        target_inventory: float = 0.0,
    ) -> None:
        self._max_inventory = max_inventory
        self._target_inventory = target_inventory
        self._inventory: float = 0.0
        self._realized_pnl: float = 0.0
        self._trade_count: int = 0

    def update(self, fill_side: str, fill_qty: float, fill_price: float) -> None:
        """Update inventory after a fill.

        A buy fill increases inventory; a sell fill decreases it.
        Realized PnL is updated for fills that reduce inventory
        (closing trades).

        Args:
            fill_side: "buy" or "sell".
            fill_qty: Quantity filled (always positive).
            fill_price: Price at which the fill occurred.
        """
        if fill_qty <= 0:
            return

        if fill_side == "buy":
            new_inventory = self._inventory + fill_qty
        elif fill_side == "sell":
            new_inventory = self._inventory - fill_qty
        else:
            return

        # Compute realized PnL for the closing portion
        if self._inventory > 0 and new_inventory < self._inventory:
            # Long position being reduced
            closed_qty = min(fill_qty, self._inventory) if fill_side == "sell" else 0.0
            if closed_qty > 0:
                self._realized_pnl += closed_qty * fill_price - closed_qty * 0.0  # avg cost unknown; mark at fill
        elif self._inventory < 0 and new_inventory > self._inventory:
            # Short position being reduced
            closed_qty = min(fill_qty, abs(self._inventory)) if fill_side == "buy" else 0.0
            if closed_qty > 0:
                self._realized_pnl += closed_qty * 0.0 - closed_qty * fill_price  # avg cost unknown

        self._inventory = new_inventory
        self._trade_count += 1

    def can_accept(self, side: str, qty: float) -> bool:
        """Check whether a fill would exceed the maximum inventory.

        Args:
            side: "buy" or "sell".
            qty: Quantity to fill (always positive).

        Returns:
            True if the fill is within inventory limits.
        """
        if qty <= 0:
            return True

        if side == "buy":
            projected = self._inventory + qty
        elif side == "sell":
            projected = self._inventory - qty
        else:
            return False

        return abs(projected) <= self._max_inventory

    def get_inventory(self) -> float:
        """Return the current net inventory."""
        return self._inventory

    def get_utilization(self) -> float:
        """Return inventory utilization as |inventory| / max_inventory.

        Returns:
            Utilization ratio in [0, 1+] (can exceed 1 if limits were breached).
        """
        if self._max_inventory == 0:
            return 0.0
        return abs(self._inventory) / self._max_inventory

    def reset(self) -> None:
        """Reset inventory state to initial values."""
        self._inventory = 0.0
        self._realized_pnl = 0.0
        self._trade_count = 0


def skew_quotes(
    quotes: ASQuotes,
    inventory: float,
    max_inventory: float,
    skew_factor: float = 0.5,
) -> ASQuotes:
    """Skew quotes based on current inventory to encourage inventory reduction.

    When long (inventory > 0), both bid and ask are lowered to encourage
    selling (hitting our ask) and discourage buying (hitting our bid).
    When short (inventory < 0), both are raised to encourage buying.

    The skew magnitude is proportional to inventory utilization and the
    spread, scaled by skew_factor.

    Args:
        quotes: Original AS quotes to skew.
        inventory: Current net inventory.
        max_inventory: Maximum allowed absolute inventory.
        skew_factor: Sensitivity of skew to inventory (0 = no skew, 1 = full).

    Returns:
        New ASQuotes with skewed bid/ask prices.
    """
    if max_inventory == 0 or inventory == 0:
        return quotes

    utilization = inventory / max_inventory
    skew_amount = skew_factor * utilization * (quotes.spread / 2.0)

    return replace(
        quotes,
        bid_price=quotes.bid_price - skew_amount,
        ask_price=quotes.ask_price - skew_amount,
    )


def adjust_sizes(
    quotes: ASQuotes,
    inventory: float,
    max_inventory: float,
    base_size: float,
) -> ASQuotes:
    """Adjust bid/ask sizes to favor the side that reduces inventory.

    When long, the ask size is increased (encourage sellers to hit our ask)
    and the bid size is decreased. When short, the opposite.

    The size adjustment scales linearly with inventory utilization.

    Args:
        quotes: Original AS quotes.
        inventory: Current net inventory.
        max_inventory: Maximum allowed absolute inventory.
        base_size: Base order size for both sides.

    Returns:
        New ASQuotes with adjusted bid/ask sizes.
    """
    if max_inventory == 0 or inventory == 0:
        return replace(quotes, bid_size=base_size, ask_size=base_size)

    utilization = min(abs(inventory) / max_inventory, 1.0)
    # Scale the reducing side up and the increasing side down
    reducing_mult = 1.0 + utilization
    increasing_mult = 1.0 - utilization * 0.5  # don't go below 0.5x

    if inventory > 0:
        # Long: want to sell -> increase ask size, decrease bid size
        return replace(
            quotes,
            bid_size=base_size * increasing_mult,
            ask_size=base_size * reducing_mult,
        )
    else:
        # Short: want to buy -> increase bid size, decrease ask size
        return replace(
            quotes,
            bid_size=base_size * reducing_mult,
            ask_size=base_size * increasing_mult,
        )

"""Portfolio state tracker — current vs target weight drift."""

from __future__ import annotations


class PortfolioState:
    """Tracks current portfolio state for rebalance decisions.

    Args:
        initial_cash: Starting cash in quote currency.
    """

    def __init__(self, initial_cash: float = 100_000.0) -> None:
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._positions: dict[str, float] = {}
        self._prices: dict[str, float] = {}

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def total_equity(self) -> float:
        equity = self._cash
        for sym, qty in self._positions.items():
            equity += qty * self._prices.get(sym, 0.0)
        return equity

    def set_cash(self, cash: float) -> None:
        self._cash = cash

    def update_positions(
        self,
        positions: dict[str, float],
        prices: dict[str, float],
    ) -> None:
        self._positions = dict(positions)
        self._prices.update(prices)

    def get_position_value(self, symbol: str) -> float:
        qty = self._positions.get(symbol, 0.0)
        price = self._prices.get(symbol, 0.0)
        return qty * price

    def get_current_weights(self) -> dict[str, float]:
        eq = self.total_equity
        if eq <= 0:
            return {}
        weights: dict[str, float] = {}
        # Cash weight
        if self._cash > 0:
            weights["_cash"] = self._cash / eq
        for sym, qty in self._positions.items():
            value = qty * self._prices.get(sym, 0.0)
            if value > 0:
                weights[sym] = value / eq
        return weights

    def compute_drift(self, target_weights: dict[str, float]) -> dict[str, float]:
        """Compute weight drift: target - current for each symbol.

        Positive drift = need to buy. Negative = need to sell.
        """
        current = self.get_current_weights()
        all_symbols = set(target_weights) | set(current)
        drift: dict[str, float] = {}
        for sym in all_symbols:
            if sym == "_cash":
                continue
            tw = target_weights.get(sym, 0.0)
            cw = current.get(sym, 0.0)
            drift[sym] = round(tw - cw, 8)
        return drift

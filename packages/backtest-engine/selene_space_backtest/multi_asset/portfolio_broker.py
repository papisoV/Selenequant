"""Shared-cash position book used by the multi-symbol rebalancer.

Long-only accounting: sells never exceed what is held, and every
symbol funds itself from the single cash balance rather than
per-symbol sub-accounts. Valuations happen only when the caller
supplies prices, so between markings the book's worth is stale by
design — ``total_equity`` reflects the latest marking only.
"""

from __future__ import annotations

_FLAT_TOLERANCE = 1e-12


class MultiAssetPortfolio:
    """Long-only book where every symbol draws from one cash pool.

    Equity marking is caller-driven: until :meth:`mark_to_market`
    runs, holdings contribute nothing to ``total_equity``, so a
    freshly built portfolio reads as its seed cash alone. Each
    marking appends one point to the equity curve.
    """

    def __init__(
        self,
        initial_cash: float = 10_000.0,
    ) -> None:
        self._seed_capital = float(initial_cash)
        self._cash = self._seed_capital
        self._book = {}
        self._marks = []
        self._unit_values = {}

    def get_position(self, symbol: str) -> float:
        """Units currently held for ``symbol`` (zero when never traded)."""
        try:
            return self._book[symbol]
        except KeyError:
            return 0.0

    def get_all_positions(self) -> dict[str, float]:
        return {sym: units for sym, units in self._book.items()}

    def apply_buy(
        self,
        symbol: str,
        quantity: float,
        price: float,
        fee: float = 0.0,
    ) -> None:
        """Debit cost plus fee, floored at zero, then credit the units."""
        debit = (quantity * price) + fee
        post_debit = self._cash - debit
        self._cash = post_debit if post_debit > 0.0 else 0.0
        prior_units = self.get_position(symbol)
        self._book[symbol] = prior_units + quantity

    def apply_sell(
        self,
        symbol: str,
        quantity: float,
        price: float,
        fee: float = 0.0,
    ) -> None:
        """Sell only what is actually held (long-only); fee always applies."""
        held = self.get_position(symbol)
        tradable = max(held, 0.0)
        executed = min(quantity, tradable)
        self._cash += executed * price - fee
        leftover = tradable - executed
        self._book[symbol] = leftover if abs(leftover) > _FLAT_TOLERANCE else 0.0

    def mark_to_market(self, latest_prices: dict[str, float]) -> None:
        """Price every holding at the newest quotes and log one point.

        Missing symbols value at zero; the resulting per-symbol
        valuations stay cached until the next marking replaces them.
        """
        valued = {}
        for sym in self._book:
            quote = latest_prices.get(sym)
            valued[sym] = self._book[sym] * (quote if quote is not None else 0.0)
        self._unit_values = valued
        holding_total = sum(self._unit_values.values())
        self._marks.append(self._cash + holding_total)

    @property
    def total_equity(self) -> float:
        """Cash plus holdings valued at the most recent marking."""
        return self._cash + sum(self._unit_values.values())

    @property
    def equity_curve(self) -> list[float]:
        return [*self._marks]

    @property
    def cash(self) -> float:
        return self._cash

    def reset(self) -> None:
        """Restore seed cash; positions, marks, and unit values drop."""
        self._book = {}
        self._marks = []
        self._unit_values = {}
        self._cash = self._seed_capital

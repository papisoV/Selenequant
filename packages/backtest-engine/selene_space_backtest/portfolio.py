"""Cash-and-position ledger backing the single-symbol backtest engine."""


from selene_space_types.trading import Fill as _FillT
from selene_space_types.trading import OrderSide as _OrderSideT

_POSITION_EPSILON = 1e-12


class Portfolio:
    """Ledger keeping cash, net positions, equity marks, cost attribution consistent."""

    def __init__(
        self,
        initial_cash: float = 10_000.0,
    ) -> None:
        self._opening_balance = float(initial_cash)
        self._balance = self._opening_balance
        self._holdings: dict[str, float] = {}
        self._funding_log: dict[str, float] = {}
        self._borrow_log: dict[str, float] = {}
        self._curve_pts: list[float] = []

    @property
    def cash(
        self,
    ) -> float:
        """Settled balance; drops on buys and every cost accrual."""
        return self._balance

    @property
    def positions(
        self,
    ) -> dict[str, float]:
        """Live mapping of net share counts by symbol."""
        return self._holdings

    def get_position_quantity(
        self,
        symbol: str,
    ) -> float:
        """Net units for ``symbol``; zero when it was never traded."""
        try:
            return self._holdings[symbol]
        except KeyError:
            return 0.0

    def _accrue_cost(
        self, bucket: dict[str, float], symbol: str, amount: float
    ) -> None:
        """Shared mechanics behind the two cost-accrual entry points."""
        self._balance -= amount
        bucket[symbol] = bucket.get(symbol, 0.0) + amount

    def apply_funding(
        self,
        symbol: str,
        amount: float,
    ) -> None:
        """Debit one perpetual-funding payment, attributed to ``symbol``.

        Positive amounts are paid by longs; negative amounts credit the
        account when a short receives funding.
        """
        self._accrue_cost(self._funding_log, symbol, amount)

    def apply_borrow_fee(
        self,
        symbol: str,
        amount: float,
    ) -> None:
        """Debit one short-borrow charge, attributed to ``symbol``."""
        self._accrue_cost(self._borrow_log, symbol, amount)

    def accrued_funding(
        self,
    ) -> dict[str, float]:
        """Snapshot of funding paid per symbol since construction/reset."""
        return {**self._funding_log}

    def borrow_fee_accrued(
        self,
    ) -> dict[str, float]:
        """Snapshot of borrow fees paid per symbol since construction/reset."""
        return {**self._borrow_log}

    def apply_fill(
        self,
        fill: _FillT,
        side: _OrderSideT,
    ) -> None:
        """Settle one broker fill into cash and the net-position map.

        BUY debits cost-plus-fee and adds units; SELL credits proceeds
        minus fee and removes them. Anything other than BUY/SELL raises
        rather than being silently interpreted. Quantities within
        ``_POSITION_EPSILON`` of zero snap flat to stop rounding dust.
        """
        price, qty, fee = (
            float(fill.price),
            float(fill.quantity),
            float(fill.fee),
        )
        going_long = side is _OrderSideT.BUY or side == _OrderSideT.BUY
        going_short = side is _OrderSideT.SELL or side == _OrderSideT.SELL
        if not going_long and not going_short:
            raise ValueError(f"Portfolio cannot apply fill with side {side!r}")

        direction = 1.0 if going_long else -1.0
        cash_delta = -(price * qty + fee) if going_long else price * qty - fee
        self._balance += cash_delta

        symbol = fill.symbol
        merged = self.get_position_quantity(symbol) + direction * qty
        if abs(merged) < _POSITION_EPSILON:
            merged = 0.0
        self._holdings[symbol] = merged

    def update_equity(
        self,
        symbol: str,
        current_price: float,
    ) -> None:
        """Append one curve point: cash plus holdings marked at this price.

        Only the named symbol's holdings take this call's price — the
        single-symbol engine marks exactly once per bar with that
        bar's close.
        """
        marked_holdings = self.get_position_quantity(symbol)
        point_value = marked_holdings * current_price
        self._curve_pts.append(point_value + self._balance)

    def get_equity_curve(
        self,
    ) -> list[float]:
        """Defensive copy: callers may mutate the result freely."""
        return [*self._curve_pts]

    def reset(
        self,
    ) -> None:
        """Back to opening state: seed cash restored, flat, curve cleared."""
        self._balance = self._opening_balance
        self._holdings.clear()
        self._curve_pts.clear()

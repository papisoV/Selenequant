"""Commission models for backtesting.

All commission models implement the ``CommissionModel`` Protocol so they
can be injected into ``SimulatedBroker`` and ``BacktestConfig``.

The canonical model is ``MakerTakerCommission`` — it mirrors the
maker/taker split used by paper fills (``selene_space_paper.fills``) and
by real crypto exchanges: limit orders that provide liquidity pay the
maker rate; market/stop-market orders that take liquidity pay the taker
rate (typically higher). For backtest↔paper commission alignment, prefer
``MakerTakerCommission`` over the legacy single-rate ``PercentageCommission``.
"""

from __future__ import annotations

from typing import Protocol

from selene_space_types.trading import OrderType


class CommissionModel(Protocol):
    """Unified commission interface.

    Models that distinguish maker vs taker should also implement
    ``calculate_with_type``; ``SimulatedBroker`` prefers that method when
    available so that limit orders pay maker and market orders pay taker.
    """

    def calculate(self, price: float, quantity: float) -> float:
        """Return the fee for a fill at *price* for *quantity*.

        Args:
            price: Fill price.
            quantity: Fill quantity.

        Returns:
            Fee amount in quote currency.
        """
        ...

    def calculate_with_type(
        self,
        price: float,
        quantity: float,
        *,
        order_type: OrderType,
    ) -> float:
        """Order-type-aware fee — optional override.

        Models without maker/taker distinction fall through to ``calculate``
        so callers (broker, engine) can use a single code path regardless
        of the injected model.
        """
        ...


def _has_type_aware(model: CommissionModel) -> bool:
    """True if *model* overrides ``calculate_with_type`` with its own logic."""
    method = getattr(type(model), "calculate_with_type", None)
    if method is None:
        return False
    return method.__qualname__ != "CommissionModel.calculate_with_type"


class NoCommission:
    """Zero commission."""

    def calculate(self, price: float, quantity: float) -> float:
        return 0.0

    def calculate_with_type(
        self,
        price: float,
        quantity: float,
        *,
        order_type: OrderType,
    ) -> float:
        return self.calculate(price, quantity)


class PercentageCommission:
    """Single-rate percentage commission.

    Default rate is 0.1 % (10 bps), typical for crypto spot trading.

    For backtest↔paper commission alignment (maker/taker split), prefer
    ``MakerTakerCommission`` — this model is kept for backward
    compatibility and for venues that don't distinguish maker/taker
    (e.g. some DEX aggregators).

    Args:
        rate: Commission rate as a decimal (0.001 = 0.1%).
    """

    def __init__(self, rate: float = 0.001) -> None:
        if rate < 0:
            raise ValueError("Commission rate must be non-negative.")
        self._rate = rate

    def calculate(self, price: float, quantity: float) -> float:
        return price * quantity * self._rate

    def calculate_with_type(
        self,
        price: float,
        quantity: float,
        *,
        order_type: OrderType,
    ) -> float:
        return self.calculate(price, quantity)


def _is_taker(order_type: OrderType) -> bool:
    """True if the order type pays the taker fee.

    Mirrors ``selene_space_paper.fills.try_fill``: MARKET and STOP_MARKET
    take liquidity; LIMIT and STOP_LIMIT provide liquidity (when filled).
    """
    return order_type in (OrderType.MARKET, OrderType.STOP_MARKET)


class MakerTakerCommission:
    """Maker/taker commission — mirrors real-exchange fee structure.

    Limit orders that provide liquidity pay ``maker_rate``; market and
    stop-market orders that take liquidity pay ``taker_rate`` (typically
    higher). For crypto spot, typical rates are 0.1% maker / 0.1% taker
    on Binance spot, but futures and some venues differentiate (e.g.
    0.02% maker / 0.05% taker on Binance USD-M futures).

    Args:
        maker_rate: Maker fee rate as a decimal (default 0.001 = 0.1%).
        taker_rate: Taker fee rate as a decimal (default 0.001 = 0.1%).
    """

    def __init__(
        self,
        maker_rate: float = 0.001,
        taker_rate: float = 0.001,
    ) -> None:
        if maker_rate < 0:
            raise ValueError("maker_rate must be non-negative.")
        if taker_rate < 0:
            raise ValueError("taker_rate must be non-negative.")
        self._maker_rate = maker_rate
        self._taker_rate = taker_rate

    @property
    def maker_rate(self) -> float:
        return self._maker_rate

    @property
    def taker_rate(self) -> float:
        return self._taker_rate

    def calculate(self, price: float, quantity: float) -> float:
        """Fallback — uses taker rate (conservative for unknown order types)."""
        return price * quantity * self._taker_rate

    def calculate_with_type(
        self,
        price: float,
        quantity: float,
        *,
        order_type: OrderType,
    ) -> float:
        rate = self._taker_rate if _is_taker(order_type) else self._maker_rate
        return price * quantity * rate

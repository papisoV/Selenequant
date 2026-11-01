"""The bar-consuming interface every backtest-compatible strategy implements.

Engines in this package drive strategies through this structural
contract: no inheritance is required, any object whose ``on_kline``
and ``reset`` match the signatures below qualifies, and ``isinstance``
checks pass at runtime thanks to :func:`typing.runtime_checkable`.

Execution-model guarantees the caller provides
----------------------------------------------
* ``on_kline`` sees only CLOSED bars, in strict chronological order,
  one call per bar per run.
* A returned Signal becomes an order that fills no earlier than the
  NEXT bar's open — engines never execute against the signaling bar,
  which keeps the simulation free of same-bar lookahead.
* 返回对象二分:带 ``order_type`` 属性的 TradeIntent 形状对象被直译为
  同类型订单(LIMIT/STOP_MARKET/STOP_LIMIT/IOC;限价取 ``price``,
  止损价取 ``risk_params["stop_price"]``),平仓单同样继承类型语义;
  其余枚举信号默认市价单。撮合规则表见
  ``internal-plan``。
* ``reset`` restores the freshly-constructed state (empty warm-up
  buffers, flat bookkeeping) so one instance can serve repeated runs
  without cross-run contamination.

What implementors must NOT assume
---------------------------------
* Nothing outside the Signal return value is read back; strategies
  that need order acknowledgements must track them internally.
* Bars may arrive for any symbol and any interval; multi-symbol runs
  interleave symbols in timestamp order.
* The engine never mutates the returned Signal object after handoff.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - annotation-only imports
    from selene_space_types.agent import TradeIntent
    from selene_space_types.market import KlineEvent
    from selene_space_types.strategy import Signal


@runtime_checkable
class StrategyRunner(Protocol):
    """Anything the event-loop engines can step forward bar by bar.

    Implementors hold their own indicator and history state between
    calls; engines never inspect that state. See the module docstring
    for the ordering and fill-timing guarantees the engine makes in
    exchange.
    """

    def on_kline(self, event: KlineEvent) -> Signal | TradeIntent | None:
        """React to one closed kline; return a trade decision, else None.

        The returned object is binary:
        * a :class:`TradeIntent` (or a duck-typed shape carrying an
          ``order_type`` attribute) is translated verbatim into an order of
          the same type — LIMIT/STOP_MARKET/STOP_LIMIT/IOC take ``price`` and
          ``risk_params["stop_price"]`` where present;
        * a plain :class:`Signal` (no ``order_type``) defaults to a market
          order.
        """
        ...

    def reset(self) -> None:
        """Drop all per-run state ahead of a fresh backtest."""
        ...

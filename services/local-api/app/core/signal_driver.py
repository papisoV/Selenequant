"""SignalDrivenExecutionAdapter — drive a live execution engine from TradeIntent.

P1-4 (spec §5 方案 A): a strategy writes its decision once as an explicit
:class:`TradeIntent`; the backtest engine's typed passthrough and this adapter
both consume the same contract. The adapter is a *client* of the
``ExecutionEngine`` Protocol (it never implements it): it translates one
``TradeIntent`` into an ``engine.place_order(...)`` call, and routes the
engine's ``on_fill`` / ``on_order_update`` events back to the strategy's
optional hooks.

Design notes
------------
* Only ``account_id`` / ``quantity`` / ``price`` / ``stop_price`` / ``side`` /
  ``order_type`` are always forwarded — this matches the 5 execution backends
  (paper / shadow / live / stock / ashare all accept these).
* ``supports_mark_price`` backends additionally receive ``mark_price`` and
  ``signal_timestamp_ms`` (mirroring ``strategy_runner._execute_signal`` and
  ``test_flag_signature_contract``).
* Backpressure is best-effort: strategies that do not expose ``on_fill`` /
  ``on_order_update`` are left untouched — the events are simply dropped,
  never raised. Stock / ashare backends with no-op subscription stubs degrade
  to "place-only, no backpressure" without error (B4).
* This file is a NEW "signal direct-drive" deployment seam; the existing
  ``strategy_runner`` path is left untouched.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - annotation-only imports
    from selene_space_types.agent import TradeIntent

logger = logging.getLogger(__name__)


class SignalDrivenExecutionAdapter:
    """Translate TradeIntent returns into live ExecutionEngine orders.

    Parameters
    ----------
    engine:
        Any object exposing ``place_order`` plus optional ``on_fill`` /
        ``on_order_update`` subscriptions (paper / shadow / live / stock /
        ashare all qualify structurally).
    account_id:
        Account to place orders against.
    strategy:
        Optional consumer for fill/order backpressure. When the strategy
        exposes ``on_fill(fill)`` / ``on_order_update(order)`` those hooks are
        called; a strategy without them simply ignores the events.
    """

    def __init__(
        self,
        engine: Any,
        account_id: str,
        strategy: Any | None = None,
    ) -> None:
        self._engine = engine
        self._account_id = account_id
        self._strategy = strategy
        self._subscribed = False

    # ------------------------------------------------------------------
    # Primary entry: one TradeIntent → one place_order
    # ------------------------------------------------------------------

    async def on_kline(
        self,
        intent: TradeIntent,
        *,
        mark_price: float | None = None,
    ) -> Any:
        """Place the order described by a strategy TradeIntent.

        Returns the engine's Order record. ``mark_price`` is the current
        reference price supplied by the driver loop; it is forwarded only to
        backends that declare ``supports_mark_price``.
        """
        engine = self._engine
        stop_price = intent.risk_params.get("stop_price")
        kwargs: dict[str, Any] = {
            "account_id": self._account_id,
            "symbol": intent.symbol,
            "side": intent.side,
            "order_type": intent.order_type,
            "quantity": intent.quantity,
            "price": intent.price,
            "stop_price": stop_price,
        }

        supports_mark = bool(getattr(engine, "supports_mark_price", False))
        if supports_mark:
            kwargs["mark_price"] = (
                mark_price if mark_price is not None else intent.price
            )
            kwargs["signal_timestamp_ms"] = intent.timestamp

        return await engine.place_order(**kwargs)

    # ------------------------------------------------------------------
    # Backpressure: engine events → strategy hooks (best-effort)
    # ------------------------------------------------------------------

    def _subscribe(self) -> None:
        """Register fill/order callbacks on the engine when available.

        Idempotent: safe to call multiple times. Stock / ashare backends
        implement no-op subscription stubs, so registration never raises.
        """
        if self._subscribed:
            return
        engine = self._engine
        try:
            if hasattr(engine, "on_fill"):
                engine.on_fill(self._on_fill_event)
            if hasattr(engine, "on_order_update"):
                engine.on_order_update(self._on_order_event)
        except Exception:  # pragma: no cover - defensive
            logger.warning(
                "signal_driver: subscription registration failed on %s",
                type(engine).__name__,
                exc_info=True,
            )
        self._subscribed = True

    def _on_fill_event(self, fill: Any) -> None:
        """Route one engine fill to the strategy's optional on_fill hook."""
        strategy = self._strategy
        if strategy is None:
            return
        on_fill = getattr(strategy, "on_fill", None)
        if callable(on_fill):
            try:
                on_fill(fill)
            except Exception:
                logger.debug(
                    "signal_driver: strategy on_fill hook failed", exc_info=True
                )

    def _on_order_event(self, order: Any) -> None:
        """Route one engine order update to the strategy's optional hook."""
        strategy = self._strategy
        if strategy is None:
            return
        on_order_update = getattr(strategy, "on_order_update", None)
        if callable(on_order_update):
            try:
                on_order_update(order)
            except Exception:
                logger.debug(
                    "signal_driver: strategy on_order_update hook failed",
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # StrategyRunner-compatible lifecycle (unused by the engine loop, but
    # present so the adapter can stand in for a strategy in driver wiring).
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """No per-run state to clear; kept for protocol symmetry."""
        return None

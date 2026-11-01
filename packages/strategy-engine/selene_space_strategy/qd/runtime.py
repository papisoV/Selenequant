"""QuantDinger script-runtime adapter: drive ``handle_data`` bar by bar.

:class:`QDStrategyRuntime` satisfies the selene ``StrategyRunner`` contract
(``on_kline -> Signal | None`` + ``reset``) by executing a compiled
QuantDinger strategy against the selene next-bar-open engine.

Convergence model
-----------------
QuantDinger reconciles ``order_target_percent`` to a *target weight* the
broker chases continuously; the strategy re-derives the target every bar
from its own ``g.*`` state. Selene instead takes one ordering decision per
bar and delivers fills at the *next* bar open — so a close decided at bar N
only materializes at bar N+1. To bridge that, the runtime persists the last
converged net target across bars and re-reconciles: even when the strategy
goes silent (its ``rebalance_deadband`` lockout, exactly the case right
after a flip/close decision it believes already landed), the runtime keeps
driving the position to that target until the strategy overrides it. This
makes flips and reductions resolve through a close t bar + reopen t+1 with
no deadband lockout, at the cost of one bar's gap.

Engine fills: the engine's ``_closing_target`` honors ``signal.quantity``
as a *partial* reduce size for ``qd:`` adapter signals (template strategies
set quantity as a target size and expect full closes, so the honor stays
scoped). Weight reductions therefore express as one reduce order for the
delta, same-sign increases as pyramiding orders (``allow_pyramiding=True``),
and flips as full-close + reopen at the target on the next bar.
"""

from __future__ import annotations

import pandas as pd

from selene_space_backtest.portfolio import Portfolio
from selene_space_strategy.qd.contract import QDCompiledStrategy
from selene_space_strategy.qd.context import (
    QDContext,
    QDManifest,
    QDParams,
    QDPosition,
    QDSymbolMapping,
)
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide

_QUANTITY_EPS = 1e-9
_WEIGHT_EPS = 5e-3  # reconcile only when the gap is material (strategy gates 0.30)

#: QD ``frequencies.py`` bar lengths in seconds; the resampler and the
#: closed-bar visibility cutoff quantize to these.
FREQUENCY_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14_400,
    "1d": 86_400,
    "1w": 604_800,
}


def _frequency_seconds(freq: str) -> int:
    return FREQUENCY_SECONDS.get(freq.strip().lower(), FREQUENCY_SECONDS["1d"])


class QDStrategyRuntime:
    """Turn one compiled QD strategy into a selene StrategyRunner."""

    def __init__(
        self,
        compiled: QDCompiledStrategy,
        *,
        symbol: str,
        interval: str = "1h",
    ) -> None:
        self._compiled = compiled
        self._symbol = symbol
        self._interval = interval
        self._manifest = QDManifest()
        self._portfolio: Portfolio | None = None
        self._symbol_map = QDSymbolMapping(selene_symbol=symbol)
        self._buffer: list[KlineEvent] = []
        self._current_ts: int | None = None
        self._pending_weight = 0.0
        self._pending_reason = "qd-init"
        self._order_weights: dict[str, float] = {}
        self._last_order_reason: str | None = None
        self._order_stop_pct: float | None = None
        self._order_tp_pct: float | None = None
        self._pos_side: str | None = None
        self._entry_price = 0.0
        self._stop_pct: float | None = None
        self._tp_pct: float | None = None
        self.reset()

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def interval(self) -> str:
        return self._interval

    @property
    def manifest(self) -> QDManifest:
        return self._manifest

    def _require_portfolio(self) -> Portfolio:
        """The engine's ledger, injected before the first bar.

        ``BacktestEngine.run`` hands the live ``Portfolio`` to any strategy
        exposing ``_portfolio``; target weights are converted to quantities
        against it, so a run without injection cannot be reconciled.
        """
        if self._portfolio is None:
            raise RuntimeError(
                "QDStrategyRuntime._portfolio not injected; the engine must "
                "set strategy._portfolio after constructing its Portfolio"
            )
        return self._portfolio

    # -- StrategyRunner -----------------------------------------------------

    def reset(self) -> None:
        """Fresh g state, empty buffer, re-declared manifest, flat target."""
        self._compiled.new_g()
        self._manifest = QDManifest()
        self._buffer = []
        self._current_ts = None
        self._pending_weight = 0.0
        self._pending_reason = "qd-init"
        self._order_stop_pct = None
        self._order_tp_pct = None
        self._pos_side = None
        self._entry_price = 0.0
        self._stop_pct = None
        self._tp_pct = None
        if self._compiled.initialize is not None:
            self._compiled.initialize(self._make_context())

    def on_kline(self, event: KlineEvent) -> Signal | None:
        self._require_portfolio()
        if event.symbol != self._symbol:
            raise ValueError(
                f"strategy symbol {event.symbol!r} does not match runtime "
                f"symbol {self._symbol!r} (engine is single-symbol)"
            )
        self._buffer.append(event)
        # QD fires handle_data at the driving bar's open time; the engine
        # delivers bar T when it closes, which is QD's next iteration --
        # clock = T_open + f(driving).
        self._current_ts = (
            int(event.timestamp) + _frequency_seconds(self._interval) * 1000
        )
        if len(self._buffer) < self._manifest.warmup:
            return None

        self._bind_api()
        ctx = self._make_context()
        self._order_weights = {}
        self._last_order_reason = None
        self._order_stop_pct = None
        self._order_tp_pct = None
        self._compiled.handle_data(ctx, ctx.data)

        target = self._resolve_target(event)
        if self._protection_triggered(event):
            self._flat()
            self._pending_weight = 0.0
            self._pending_reason = "qd-protection"
            target = 0.0
        return self._signal_from_target(target, event)

    # -- API surface bound into the strategy namespace ----------------------

    def _make_context(self, event: KlineEvent | None = None) -> QDContext:
        return QDContext(
            self._manifest,
            QDParams(self._compiled.param_defaults),
            data_symbol=self._symbol,
            current_ts=int(event.timestamp) if event is not None else self._current_ts,
            history_fn=self._data_history,
            current_fn=self._data_current,
        )

    def _bind_api(self) -> None:
        """Rebind get_history / get_position / order_target_percent for this bar."""
        module = self._compiled.module_globals
        module["get_history"] = self._get_history
        module["get_position"] = self._get_position
        module["order_target_percent"] = self._order_target_percent

    def _get_history(
        self,
        count: int,
        freq: str | None = None,
        fields: list[str] | None = None,
        symbol: str | None = None,
    ) -> pd.DataFrame:
        """Return the last ``count`` bars (current included) as a RangeIndex frame."""
        symbol = self._symbol_map.resolve(symbol)
        if isinstance(fields, str):
            fields = [fields]
        window = self._buffer[-int(count) :]
        fields = list(fields or ["open", "high", "low", "close"])
        data = {
            field: [float(getattr(event, field, float("nan"))) for event in window]
            for field in fields
        }
        return pd.DataFrame(data, index=range(len(window)))

    def _data_history(
        self,
        symbols: object,
        *,
        count: int,
        fields: object = None,
        frequency: object = None,
    ) -> pd.DataFrame:
        """QD ``data.history``: resampled bars visible under the closed-bar rule.

        The engine delivers each driving bar at its close, which is QD's
        ``include_current=False`` clock: ``current_dt = T_open + f(driving)``.
        A requested-frequency bar is visible iff its open time is at or before
        ``current_dt - f(requested)`` -- i.e. only after the bar's own close.
        Finer-than-driving requests have no data to resample and refuse
        loudly instead of fabricating a frame.
        """
        requested = str(frequency or self._interval).strip().lower()
        driving = self._interval.strip().lower()
        if _frequency_seconds(requested) < _frequency_seconds(driving):
            raise ValueError(
                f"data.history frequency {requested!r} is finer than the driving "
                f"bar {driving!r}; no source data to resample"
            )
        symbol_arg = symbols if isinstance(symbols, str) else None
        if symbol_arg is None and isinstance(symbols, (list, tuple)):
            symbol_arg = str(symbols[0]) if symbols else None
        symbol = self._symbol_map.resolve(symbol_arg)
        if symbol != self._symbol:
            raise ValueError(
                f"data.history for {symbol!r} outside runtime symbol {self._symbol!r}"
            )
        if isinstance(fields, str):
            selected = [fields]
        elif isinstance(fields, (list, tuple)):
            selected = [str(f).strip().lower() for f in fields]
        else:
            selected = []

        cutoff_ms = (self._current_ts or 0) - _frequency_seconds(requested) * 1000
        if cutoff_ms < 0 or not self._buffer:
            return pd.DataFrame(
                columns=selected if selected else ["open", "high", "low", "close"]
            )

        # Resampling only needs enough driving bars to cover ``count`` buckets
        # (bucket alignment can add one bucket of slack); scanning the full
        # buffer per call is O(n) and sources call this every bar.
        req_ms = _frequency_seconds(requested) * 1000
        drive_ms = _frequency_seconds(driving) * 1000
        need_bars = int(count) * max(req_ms // drive_ms, 1) + req_ms // drive_ms + 2
        window = self._buffer[-need_bars:]
        frame = pd.DataFrame(
            {
                "timestamp": [int(e.timestamp) for e in window],
                "open": [float(e.open) for e in window],
                "high": [float(e.high) for e in window],
                "low": [float(e.low) for e in window],
                "close": [float(e.close) for e in window],
                "volume": [float(e.volume) for e in window],
            }
        )
        if req_ms > drive_ms:
            # QD keeps one frame per subscribed frequency: the HTF frame's
            # index is bucket open times, cut independently of the driving
            # frame (searchsorted side="right" on its own index). Resample
            # everything, then keep buckets whose open <= cutoff.
            frame = (
                frame.assign(bucket=frame["timestamp"] // req_ms)
                .groupby("bucket", as_index=False)
                .agg(
                    timestamp=("timestamp", "first"),
                    open=("open", "first"),
                    high=("high", "max"),
                    low=("low", "min"),
                    close=("close", "last"),
                    volume=("volume", "sum"),
                )
            )
        elif selected:
            frame = frame[["timestamp"] + [f for f in selected if f != "timestamp"]]
        visible = frame[frame["timestamp"] <= cutoff_ms]
        if selected:
            visible = visible[selected]
        return visible.tail(int(count)).reset_index(drop=True)

    def _data_current(
        self,
        symbol: object,
        field: str = "close",
        default: float = 0.0,
        *,
        frequency: object = None,
    ) -> float:
        """QD ``data.current``: last visible bar's field, NaN-guarded."""
        frame = self._data_history(symbol, count=1, fields=[field], frequency=frequency)
        if frame.empty or field not in frame.columns:
            return float(default)
        value = float(frame.iloc[-1][field])
        return value if value == value else float(default)

    def _get_position(
        self, symbol: str | None = None, position_side: str = "long"
    ) -> QDPosition:
        resolved = self._symbol_map.resolve(symbol)
        if resolved != self._symbol:
            raise ValueError(
                f"get_position for {resolved!r} outside runtime symbol {self._symbol!r}"
            )
        quantity = self._require_portfolio().get_position_quantity(self._symbol)
        avg_cost = self._entry_price if quantity else 0.0
        if position_side == "long":
            return QDPosition(
                amount=quantity if quantity > 0 else 0.0, avg_cost=avg_cost
            )
        if position_side == "short":
            return QDPosition(
                amount=-quantity if quantity < 0 else 0.0, avg_cost=avg_cost
            )
        raise ValueError(f"unknown position_side {position_side!r}")

    def _order_target_percent(
        self,
        symbol: str | None,
        weight: float,
        *,
        position_side: str = "long",
        reason: str | None = None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
    ) -> None:
        """Record one side's target weight; later calls on the same side win.

        ``stop_loss_pct`` / ``take_profit_pct`` are QD's per-order protection
        bracket: they ride the order and arm only against the position it
        opens (see :meth:`_protection_triggered`). A zero or absent value
        means no bracket, matching QD's ``> 0`` gate.
        """
        resolved = self._symbol_map.resolve(symbol)
        if resolved != self._symbol:
            raise ValueError(
                f"order_target_percent for {resolved!r} outside runtime symbol {self._symbol!r}"
            )
        if position_side not in ("long", "short"):
            raise ValueError(f"unsupported position_side {position_side!r}")
        self._order_weights[position_side] = abs(float(weight))
        if reason:
            self._last_order_reason = reason
        if stop_loss_pct is not None:
            self._order_stop_pct = min(abs(float(stop_loss_pct)), 1.0)
        if take_profit_pct is not None:
            self._order_tp_pct = min(abs(float(take_profit_pct)), 1.0)

    def _protection_triggered(self, event: KlineEvent) -> bool:
        """Trip the armed stop / take-profit against the bar's extremes.

        Mirrors QD's ``_bar_candidates`` and selene's native ``_check_risk``
        alike: the stop trips on the adverse extreme (low for longs, high for
        shorts) and the target on the favourable one. The resulting close
        materializes at the next bar open — the adapter's usual one-bar
        convergence gap, not a new source of divergence.
        """
        if self._pos_side is None or self._entry_price <= 0:
            return False
        entry = self._entry_price
        low, high = float(event.low), float(event.high)
        if self._pos_side == "long":
            if self._stop_pct and low <= entry * (1 - self._stop_pct):
                return True
            return bool(self._tp_pct and high >= entry * (1 + self._tp_pct))
        if self._stop_pct and high >= entry * (1 + self._stop_pct):
            return True
        return bool(self._tp_pct and low <= entry * (1 - self._tp_pct))

    def _flat(self) -> None:
        self._pos_side = None
        self._entry_price = 0.0
        self._stop_pct = self._tp_pct = None

    def _arm(self, side: str, quantity: float, event: KlineEvent) -> None:
        """Arm this order's bracket once it establishes a new position.

        Adding to an existing position (pyramiding) keeps the original
        bracket, so only a flat-to-open transition re-arms.
        """
        if abs(quantity) > _QUANTITY_EPS:
            return
        self._pos_side = side
        self._entry_price = float(event.close)
        self._stop_pct = self._order_stop_pct
        self._tp_pct = self._order_tp_pct

    # -- convergence ---------------------------------------------------------

    def _equity(self, event: KlineEvent) -> tuple[float, float]:
        """(price, mark-to-market equity); None-guard handled by caller."""
        portfolio = self._require_portfolio()
        quantity = portfolio.get_position_quantity(self._symbol)
        price = float(event.close)
        equity = portfolio.cash + quantity * price
        return price, equity

    def _resolve_target(self, event: KlineEvent) -> float:
        """Signed net target weight for this bar (persist-or-override)."""
        if self._order_weights:
            portfolio = self._require_portfolio()
            quantity = portfolio.get_position_quantity(self._symbol)
            price = float(event.close)
            base = abs(quantity * price / max(portfolio.cash + quantity * price, 1e-9))
            side_weight = {
                "long": base if quantity > 0 else 0.0,
                "short": base if quantity < 0 else 0.0,
            }
            for side, declared in self._order_weights.items():
                side_weight[side] = declared
            self._pending_weight = side_weight["long"] - side_weight["short"]
            if self._last_order_reason:
                self._pending_reason = self._last_order_reason
        return self._pending_weight

    def _signal_from_target(self, target: float, event: KlineEvent) -> Signal | None:
        quantity = self._require_portfolio().get_position_quantity(self._symbol)
        price, equity = self._equity(event)
        if equity <= 0 or price <= 0:
            return None
        current_weight = quantity * price / equity
        if abs(target - current_weight) < _WEIGHT_EPS:
            return None
        desired = target * equity / price
        delta = desired - quantity

        if quantity > _QUANTITY_EPS and desired <= 0:
            side, qty = OrderSide.SELL, abs(quantity)  # close long (also flip)
            self._flat()
        elif quantity < -_QUANTITY_EPS and desired >= 0:
            side, qty = OrderSide.BUY, abs(quantity)  # close short (also flip)
            self._flat()
        elif delta > _QUANTITY_EPS:
            side, qty = OrderSide.BUY, delta  # open / pyramid long
            self._arm("long", quantity, event)
        elif delta < -_QUANTITY_EPS:
            side, qty = OrderSide.SELL, -delta  # open / pyramid short
            self._arm("short", quantity, event)
        else:
            return None

        return Signal(
            strategy_id=f"qd:{self._compiled.source_name}",
            symbol=self._symbol,
            side=side,
            strength=1.0,
            timestamp=event.timestamp,
            reason=self._pending_reason or "qd-order",
            quantity=float(qty),
        )

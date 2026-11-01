"""Averaging-down ladder with exponential geometry ("DCA grid").

Structure
---------
A trend trigger (fast/slow MA cross) opens the first tranche.  From then on a
pre-computed price ladder sits below (long) or above (short) the fill:

  rung spacing  : d(1) = s;  d(i) = s + d(i-1) * g      (cumulative fraction)
  tranche size  : q(i) = base * v^(i-1)

so both the depth between rungs and the money per rung grow geometrically.
Unlike an open-ended martingale the ladder has ``max_trades`` rungs and the
final rung doubles as an anchor for the catastrophic stop.

Exit logic
----------
* take profit -- close crosses average cost * (1 ± tp)
* stop loss   -- bar's low/high pierces last-rung * (1 ∓ sl buffer)
The whole book is liquidated at once and the ladder rebuilt on the next cross.
"""

from __future__ import annotations

import math

import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


class _Rung:
    """One pre-planned averaging level."""

    __slots__ = ("index", "price", "qty_pct", "taken")

    def __init__(self, index: int, price: float, qty_pct: float) -> None:
        self.index = index          # 1-based rung number
        self.price = price          # absolute trigger price
        self.qty_pct = qty_pct      # percent-of-capital to deploy here
        self.taken = index == 1     # first rung consumed by the initial entry


class _Book:
    """Aggregated state of every filled tranche."""

    __slots__ = ("fills", "cost", "size", "avg", "rungs", "cursor", "anchor")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.fills: list[float] = []
        self.cost = 0.0            # sum(qty * fill_price), sign-free
        self.size = 0.0            # signed net quantity (+ long / - short)
        self.avg = 0.0
        self.rungs: list[_Rung] = []
        self.cursor = 1            # next unfilled rung number
        self.anchor = 0.0          # last rung price (stop anchor)


class DCAGridStrategy(BacktestStrategyTemplate):
    """MA-cross starter plus geometric averaging ladder with capped risk."""

    def __init__(
        self,
        symbol: str = "BTC/USDT", initial_capital: float = 100000.0,
        leverage: int = 1, fee_rate: float = 0.0005,
        max_bars: int = 500, long_only: bool = True,
        # trigger averages
        fast_ma: int = 10, slow_ma: int = 30, ma_type: str = "ema",
        # ladder geometry (matching user's DcaClass)
        base_size: float = 5.0, max_trades: int = 9,
        grid_spacing: float = 0.004, grid_price_scale: float = 1.19,
        grid_volume_scale: float = 1.8,
        # bracket percentages
        take_profit_pct: float = 0.015, stop_loss_pct: float = 0.05,
        enable_long: bool = True, enable_short: bool = False,
        trend_filter_enabled: bool = False, adx_length: int = 14,
        adx_threshold: float = 25.0,
        use_atr_stops: bool = False, atr_stop_period: int = 14,
        atr_sl_mult: float = 2.0, atr_tp_mult: float = 3.0,
        **kwargs,
    ) -> None:
        super().__init__(
            symbol, initial_capital, leverage, fee_rate, max_bars,
            long_only, use_atr_stops=use_atr_stops,
            atr_stop_period=atr_stop_period, atr_sl_mult=atr_sl_mult,
            atr_tp_mult=atr_tp_mult, **kwargs,
        )

        (
            self.fast_ma, self.slow_ma,
            self.base_size, self.max_trades,
            self.grid_spacing, self.grid_price_scale, self.grid_volume_scale,
            self.take_profit_pct, self.stop_loss_pct,
            self.enable_long, self.enable_short,
            self.trend_filter_enabled, self.adx_length, self.adx_threshold,
        ) = (
            fast_ma, slow_ma,
            base_size, max_trades,
            grid_spacing, grid_price_scale, grid_volume_scale,
            take_profit_pct, stop_loss_pct,
            enable_long, enable_short,
            trend_filter_enabled, adx_length, adx_threshold,
        )
        self.ma_type = ma_type.lower()

        # DCA state
        self._book = _Book()
        self._stop_loss_price = self._take_profit_price = 0.0
        self._has_initial_entry = False

    def get_parameters(self) -> dict:
        merged = super().get_parameters()
        exposed = (
            "fast_ma", "slow_ma", "ma_type", "base_size", "max_trades",
            "grid_spacing", "grid_price_scale", "grid_volume_scale",
            "take_profit_pct", "stop_loss_pct", "enable_long", "enable_short",
            "trend_filter_enabled", "adx_length", "adx_threshold",
        )
        merged.update({key: getattr(self, key) for key in exposed})
        return merged

    # ------------------------------------------------------------------
    # Ladder geometry
    # ------------------------------------------------------------------

    def _ma_series(self, data: pd.DataFrame, period: int) -> pd.Series:
        """Fast/slow moving average respecting ``ma_type``."""
        if self.ma_type == "ema":
            from selene_space_strategy.indicators.ema import EMA
            return EMA(period).calculate(data)
        return data["close"].rolling(window=period, min_periods=period).mean()

    def _cum_depth(self, i: int) -> float:
        """Cumulative fractional distance of rung ``i`` from the anchor.

        Closed form of the recurrence d(i) = s + d(i-1)*g with d(1)=s:
            d(i) = s * (g^i - 1) / (g - 1)   for g != 1
            d(i) = s * i                     for g == 1
        """
        if i <= 0:
            return 0.0
        if i == 1 or self.grid_price_scale == 1.0:
            # scale==1 collapses to arithmetic accumulation; i==1 is just s
            if self.grid_price_scale == 1.0:
                return self.grid_spacing * i
            return self.grid_spacing
        g = self.grid_price_scale
        return self.grid_spacing * (g ** i - 1.0) / (g - 1.0)

    def _rung_price(self, anchor: float, i: int, side: str) -> float:
        off = self._cum_depth(i)
        return anchor * (1 - off) if side == "BUY" else anchor * (1 + off)

    def _rung_qty_pct(self, i: int) -> float:
        return self.base_size * math.pow(self.grid_volume_scale, i - 1)

    def _plan_ladder(self, anchor: float, side: str) -> list[_Rung]:
        plan = [
            _Rung(i, self._rung_price(anchor, i, side), self._rung_qty_pct(i))
            for i in range(1, self.max_trades + 1)
        ]
        return plan

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        if len(data) < self.slow_ma + 2:
            return SignalType.HOLD

        n = len(data) - 1
        px_now = data["close"].iloc[n]

        fast = self._ma_series(data, self.fast_ma)
        slow = self._ma_series(data, self.slow_ma)
        f_t, f_p = fast.iloc[n], fast.iloc[n - 1]
        s_t, s_p = slow.iloc[n], slow.iloc[n - 1]
        if pd.isna(f_t) or pd.isna(s_t):
            return SignalType.HOLD

        bull_start = f_p <= s_p and f_t > s_t
        bear_start = f_p >= s_p and f_t < s_t

        # === FIRST TRANCHE ===
        if not self._has_initial_entry:
            if self.enable_long and bull_start:
                if self.trend_filter_enabled:
                    trending, _ = self.check_trend_filter(
                        data, self.adx_length, self.adx_threshold, direction="long"
                    )
                    if not trending:
                        return SignalType.HOLD
                return SignalType.BUY
            if self.enable_short and not self.long_only and bear_start:
                if self.trend_filter_enabled:
                    trending, _ = self.check_trend_filter(
                        data, self.adx_length, self.adx_threshold, direction="short"
                    )
                    if not trending:
                        return SignalType.HOLD
                return SignalType.SELL

        # === AVERAGING / EXIT while a book is open ===
        elif self._book.size > 0 and self._book.cursor <= self.max_trades:
            next_rung = self._book.rungs[self._book.cursor - 1]
            if px_now <= next_rung.price:
                return SignalType.BUY
            if self._take_profit_price > 0 and px_now >= self._take_profit_price:
                return SignalType.CLOSE_LONG
            if self._stop_loss_price > 0 and data["low"].iloc[n] <= self._stop_loss_price:
                return SignalType.CLOSE_LONG

        elif self._book.size < 0 and not self.long_only:
            next_rung = self._book.rungs[self._book.cursor - 1]
            if px_now >= next_rung.price:
                return SignalType.SELL
            if self._take_profit_price > 0 and px_now <= self._take_profit_price:
                return SignalType.CLOSE_SHORT
            if self._stop_loss_price > 0 and data["high"].iloc[n] >= self._stop_loss_price:
                return SignalType.CLOSE_SHORT

        return SignalType.HOLD

    # ------------------------------------------------------------------
    # Execution & bookkeeping
    # ------------------------------------------------------------------

    def _refresh_bracket(self, side: str) -> None:
        """Recompute TP/SL from current average cost and final rung."""
        avg = self._book.avg
        if self._use_atr_stops:
            direction = "long" if side == "BUY" else "short"
            self._stop_loss_price, self._take_profit_price = self._calculate_atr_stop_levels(avg, direction)
            return
        if side == "BUY":
            self._take_profit_price = avg * (1 + self.take_profit_pct)
        else:
            self._take_profit_price = avg * (1 - self.take_profit_pct)
        if self._book.rungs:
            tail = self._book.rungs[-1].price
            pad = 1 - self.stop_loss_pct if side == "BUY" else 1 + self.stop_loss_pct
            self._stop_loss_price = tail * pad

    def _flatten_book(self, price: float, timestamp: int, action: str, qty: float) -> None:
        """Realize PnL for the whole book, record it, clear state."""
        gross = (
            (price - self._book.avg) * self._book.size
            if self._book.size > 0
            else (self._book.avg - price) * abs(self._book.size)
        )
        fee = self._book.cost * self.fee_rate * 2  # entry + exit legs
        net = gross - fee
        self._capital += net
        self._record_trade(price, timestamp, action, net)
        self._book = _Book()
        self._has_initial_entry = False
        self._stop_loss_price = 0.0
        self._take_profit_price = 0.0

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        price = event.close
        stamp = event.timestamp

        if signal_type == SignalType.HOLD:
            return None

        action: OrderSide | None = None
        reason = ""
        quantity: float | None = None

        # --- initial long tranche ---
        if signal_type == SignalType.BUY and not self._has_initial_entry:
            action = OrderSide.BUY
            reason = "Golden cross - initial entry"
            self._book.rungs = self._plan_ladder(price, "BUY")
            self._book.cursor = 2
            size_pct = self._rung_qty_pct(1)
            quantity = self._capital * size_pct / 100 / price
            self._has_initial_entry = True
            self._entry_price = price
            self._entry_time = stamp

        # --- averaging add ---
        elif (
            signal_type == SignalType.BUY
            and self._has_initial_entry
            and self._book.cursor <= self.max_trades
        ):
            idx = self._book.cursor
            action = OrderSide.BUY
            reason = f"Grid level {idx} triggered"
            quantity = self._capital * self._rung_qty_pct(idx) / 100 / price
            self._book.rungs[idx - 1].taken = True
            self._book.cursor += 1

        # --- initial short tranche ---
        elif signal_type == SignalType.SELL and not self._has_initial_entry and not self.long_only:
            action = OrderSide.SELL
            reason = "Death cross - initial short entry"
            self._book.rungs = self._plan_ladder(price, "SELL")
            self._book.cursor = 2
            size_pct = self._rung_qty_pct(1)
            quantity = self._capital * size_pct / 100 / price
            self._has_initial_entry = True

        # --- flatten long book ---
        elif signal_type in (SignalType.CLOSE_LONG, SignalType.SELL) and self._book.size > 0:
            action = OrderSide.SELL
            reason = "Take profit or stop loss triggered"
            quantity = self._book.size
            self._flatten_book(price, stamp, "close_long", self._book.size)

        # --- flatten short book ---
        elif (
            signal_type == SignalType.CLOSE_SHORT
            and self._book.size < 0
            and not self.long_only
        ):
            action = OrderSide.BUY
            reason = "Take profit or stop loss triggered"
            quantity = abs(self._book.size)
            self._flatten_book(price, stamp, "close_short", abs(self._book.size))

        if action is None:
            return None

        # Update aggregate book for entries
        if action == OrderSide.BUY and signal_type != SignalType.CLOSE_SHORT and quantity:
            self._book.fills.append(price)
            self._book.size += quantity
            self._book.cost += quantity * price
            self._book.avg = self._book.cost / self._book.size
            if self._book.rungs:
                self._book.anchor = self._book.rungs[-1].price
            self._refresh_bracket("BUY")

        elif action == OrderSide.SELL and signal_type != SignalType.CLOSE_LONG and quantity and not self.long_only:
            self._book.fills.append(price)
            self._book.size -= quantity
            self._book.cost += quantity * price
            self._book.avg = self._book.cost / abs(self._book.size)
            if self._book.rungs:
                self._book.anchor = self._book.rungs[-1].price
            self._refresh_bracket("SELL")

        return Signal(
            strategy_id=f"dca-grid-{self.symbol}",
            symbol=self.symbol,
            side=action,
            strength=1.0,
            timestamp=int(__import__("time").time() * 1000),
            reason=reason,
            quantity=quantity,
        )

    def reset(self) -> None:
        super().reset()
        self._book = _Book()
        self._stop_loss_price = self._take_profit_price = 0.0
        self._has_initial_entry = False

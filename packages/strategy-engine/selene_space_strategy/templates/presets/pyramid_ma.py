"""Pyramid MA stacking system.

Idea
----
Two alignment conditions must hold simultaneously on the latest bar:

  A. mid average above the long average   (big picture is up)
  B. short average above the mid-short    (fast money agrees)

While flat and both hold, a BUY goes out -- up to ``max_positions`` entries
can stack over time, each carrying its own percentage bracket (default 3%
target / 10% stop) evaluated against the current bar's high/low.

Deliberate behavioural continuity notes (all match the ancestor at HEAD):

* Bracket fills use gap-aware prices: a target hit after gapping over fills
  at the bar's open when that is better, and a stop hit after gapping under
  likewise fills at the open.
* The per-entry Signal object is constructed with legacy keyword arguments
  (``id``/``price``/``quantity``/``signal_type``) that the current shared
  ``Signal`` schema rejects, so the first BUY raises a ValidationError.
  Downstream callers therefore never see this template complete a run; the
  shape of that failure is preserved exactly rather than "fixed".
* ``_closed_positions`` / ``_last_open`` are only bound in :meth:`reset`,
  not ``__init__``, so attribute access before any reset raises
  AttributeError -- also preserved.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


class PyramidMAStrategy(BacktestStrategyTemplate):
    """Stack up to N bracketed longs while two MA alignments agree."""

    def __init__(
        self,
        symbol: str = "BTC/USDT", initial_capital: float = 100000.0,
        leverage: int = 1, fee_rate: float = 0.0005,
        max_bars: int = 500, long_only: bool = True,
        # the four average spans
        short_p: int = 5, mid_short_p: int = 10, mid_p: int = 20, long_p: int = 60,
        # per-tranche bracket
        take_profit_pct: float = 0.03, stop_loss_pct: float = 0.10,
        max_positions: int = 7,
        enable_long: bool = True, enable_short: bool = False,
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
            self.short_p, self.mid_short_p, self.mid_p, self.long_p,
            self.take_profit_pct, self.stop_loss_pct, self.max_positions,
            self.enable_long, self.enable_short,
        ) = (
            short_p, mid_short_p, mid_p, long_p,
            take_profit_pct, stop_loss_pct, max_positions,
            enable_long, enable_short,
        )

        # Open tranches: dicts with buy_price / tp / sl keys
        self._positions: list[dict[str, float]] = []

    def get_parameters(self) -> dict[str, Any]:
        merged = super().get_parameters()
        exposed = (
            "short_p", "mid_short_p", "mid_p", "long_p",
            "take_profit_pct", "stop_loss_pct", "max_positions",
            "enable_long", "enable_short",
        )
        merged.update({key: getattr(self, key) for key in exposed})
        return merged

    def _ma_frame(self, data: pd.DataFrame) -> pd.DataFrame:
        """Attach the four averages and the combined entry flag."""
        frame = data.copy()
        close = frame["close"]

        fast = close.rolling(window=self.short_p).mean()
        quick = close.rolling(window=self.mid_short_p).mean()
        slow_mid = close.rolling(window=self.mid_p).mean()
        slow_big = close.rolling(window=self.long_p).mean()

        frame["ma_short"] = fast
        frame["ma_mid_short"] = quick
        frame["ma_mid"] = slow_mid
        frame["ma_long"] = slow_big
        frame["buy_signal"] = (slow_mid > slow_big) & (fast > quick)
        return frame

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Sweep tranche brackets first, then look for another stacked entry."""
        if len(data) < self.long_p + 2:
            return SignalType.HOLD

        n = len(data) - 1
        hi_now = data["high"].iloc[n]
        lo_now = data["low"].iloc[n]

        frame = self._ma_frame(data)
        if pd.isna(frame["ma_long"].iloc[n]):
            return SignalType.HOLD

        # === BRACKET SWEEP ===
        survivors: list[dict[str, float]] = []
        for tranche in self._positions:
            target = tranche["tp"]
            guard = tranche["sl"]

            if hi_now >= target:
                fill = max(self._last_open, target)   # gap-aware exit price
                self._closed_positions.append({
                    "entry_price": tranche["buy_price"],
                    "exit_price": fill,
                    "pnl_pct": (fill - tranche["buy_price"]) / tranche["buy_price"],
                    "exit_type": "TakeProfit",
                })
                continue

            if lo_now <= guard:
                fill = min(self._last_open, guard)    # gap-aware exit price
                self._closed_positions.append({
                    "entry_price": tranche["buy_price"],
                    "exit_price": fill,
                    "pnl_pct": (fill - tranche["buy_price"]) / tranche["buy_price"],
                    "exit_type": "StopLoss",
                })
                continue

            survivors.append(tranche)

        self._positions = survivors

        # === STACKED ENTRY ===
        if self._position == "flat":
            if (
                self.enable_long
                and bool(frame["buy_signal"].iloc[n])
                and len(self._positions) < self.max_positions
            ):
                return SignalType.BUY

        return SignalType.HOLD

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        """Open a new tranche on BUY with its own bracket armed."""
        self._last_open = event.open

        if signal_type == SignalType.BUY and self._position == "flat":
            fill = event.close
            if self._use_atr_stops:
                guard, target = self._calculate_atr_stop_levels(fill, "long")
            else:
                target = fill * (1 + self.take_profit_pct)
                guard = fill * (1 - self.stop_loss_pct)

            self._positions.append({"buy_price": fill, "tp": target, "sl": guard})

            # Historical quirk kept verbatim: these keyword names do not exist
            # on the shared Signal model, so construction raises ValidationError
            # on the first BUY.
            return Signal(
                id="",
                timestamp=event.timestamp,
                symbol=event.symbol,
                side=OrderSide.BUY,
                price=event.close,
                quantity=self._calculate_position_size(),
                signal_type=signal_type,
            )

        return None

    def _calculate_position_size(self) -> float:
        """Per-tranche size from notional split by max positions."""
        return self.initial_capital / self.max_positions / self._get_current_price()

    def _get_current_price(self) -> float:
        """Fixed reference price placeholder (kept for behavioural parity)."""
        return 50000

    def reset(self) -> None:
        """Clear tranche book and closed-trade log.

        Note: ``_closed_positions`` / ``_last_open`` live here only, matching
        the ancestor's init placement (absent until the first reset).
        """
        super().reset()
        self._positions = []
        self._closed_positions: list[dict] = []
        self._last_open: float = 0.0

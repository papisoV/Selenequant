"""CHOP-gated trend-following preset.

The Choppiness Index separates orderly tape (low readings) from
directionless tape (high readings). This template treats the index as a
gatekeeper standing in front of a plain moving-average trend filter:

* readings below ``trend_threshold`` open the gate — entries are allowed;
* readings above ``choppy_threshold`` slam it shut — any open position is
  handed back, because churn of that magnitude eats trend profits;
* in between, existing positions ride until the reference break or a
  fixed / ATR-scaled bracket closes them out.

Directional bias comes from price versus the moving average: above it,
longs may pass the gate; below it, shorts may (when two-sided trading is
enabled).

Knob reference
--------------
chop_period        lookback fed to the Choppiness Index (14)
ma_len             window of the directional average (20)
ma_type            ``sma`` or ``ema`` builds the reference line (sma)
trend_threshold    reading under which the tape counts as trending (38.2)
choppy_threshold   reading over which the tape counts as choppy (61.8)
tp_pct / sl_pct    fixed profit-target / stop-loss distances (5% / 3%)
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.indicators.chop import ChoppinessIndex
from selene_space_strategy.indicators.ema import EMA
from selene_space_strategy.indicators.sma import SMA
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class ChopGuardStrategy(BacktestStrategyTemplate):
    """Trend entries permitted only while the CHOP gate stands open.

    Flat: BUY when the tape reads trending and price tops the reference
    line; SELL (short) under the mirrored conditions when shorts enabled.
    Long: bracket levels fire first, then a choppy reading or a close under
    the reference flattens. Short: mirror image, gated behind ``long_only``.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        chop_period: int = 14,
        ma_len: int = 20,
        ma_type: str = "sma",
        trend_threshold: float = ChoppinessIndex.TREND_THRESHOLD,
        choppy_threshold: float = ChoppinessIndex.CHOPPY_THRESHOLD,
        tp_pct: float = 0.05,
        sl_pct: float = 0.03,
        enable_long: bool = True,
        enable_short: bool = False,
        use_atr_stops: bool = False,
        atr_stop_period: int = 14,
        atr_sl_mult: float = 2.0,
        atr_tp_mult: float = 3.0,
        **kwargs,
    ) -> None:
        upstream: dict[str, Any] = {
            "symbol": symbol,
            "initial_capital": initial_capital,
            "leverage": leverage,
            "fee_rate": fee_rate,
            "max_bars": max_bars,
            "long_only": long_only,
            "use_atr_stops": use_atr_stops,
            "atr_stop_period": atr_stop_period,
            "atr_sl_mult": atr_sl_mult,
            "atr_tp_mult": atr_tp_mult,
            **kwargs,
        }
        super().__init__(**upstream)

        # Template-owned knobs live in one mapping; get_parameters returns it
        # verbatim on top of the base settings. Normalisation happens here,
        # at the single point of storage.
        self._own_params: dict[str, Any] = {
            "chop_period": chop_period,
            "ma_len": ma_len,
            "ma_type": ma_type.lower(),
            "trend_threshold": float(trend_threshold),
            "choppy_threshold": float(choppy_threshold),
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "enable_long": enable_long,
            "enable_short": enable_short,
        }

        self._tp_price: float = 0.0
        self._sl_price: float = 0.0

    def get_parameters(self) -> dict[str, Any]:
        """Account-level base settings overlaid with this template's knobs."""
        return {**super().get_parameters(), **self._own_params}

    def _trend_reference(self, data: pd.DataFrame) -> pd.Series:
        builder = EMA if self._own_params["ma_type"] == "ema" else SMA
        return builder(self._own_params["ma_len"]).calculate(data)

    def _open_gate_action(self, trending: bool, close: float, reference: float) -> SignalType:
        """Verdict while flat: the gate admits trades only when trending."""
        if not trending:
            return SignalType.HOLD
        if self._own_params["enable_long"] and close > reference:
            return SignalType.BUY
        if self._own_params["enable_short"] and not self.long_only and close < reference:
            return SignalType.SELL
        return SignalType.HOLD

    def _long_housekeeping(self, high: float, low: float, tape_broken: bool) -> SignalType:
        if self._tp_price > 0 and high >= self._tp_price:
            return SignalType.CLOSE_LONG
        if self._sl_price > 0 and low <= self._sl_price:
            return SignalType.CLOSE_LONG
        if tape_broken:
            return SignalType.SELL
        return SignalType.HOLD

    def _short_housekeeping(self, high: float, low: float, tape_broken: bool) -> SignalType:
        if self._tp_price > 0 and low <= self._tp_price:
            return SignalType.CLOSE_SHORT
        if self._sl_price > 0 and high >= self._sl_price:
            return SignalType.CLOSE_SHORT
        if tape_broken:
            return SignalType.CLOSE_SHORT
        return SignalType.HOLD

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Admit trend trades through the CHOP gate; retreat when it shuts."""
        knob = self._own_params
        if len(data) < max(knob["chop_period"], knob["ma_len"]) + 2:
            return SignalType.HOLD

        tip = data.iloc[-1]
        chop_reading = ChoppinessIndex(knob["chop_period"]).calculate(data).iloc[-1]
        reference = self._trend_reference(data).iloc[-1]

        if pd.isna(chop_reading) or pd.isna(reference):
            return SignalType.HOLD

        trending = chop_reading < knob["trend_threshold"]
        gate_shut = chop_reading >= knob["choppy_threshold"]

        if self._position == "flat":
            return self._open_gate_action(trending, tip.close, reference)

        if self._position == "long":
            return self._long_housekeeping(
                tip.high, tip.low, gate_shut or tip.close < reference
            )

        if self._position == "short" and not self.long_only:
            return self._short_housekeeping(
                tip.high, tip.low, gate_shut or tip.close > reference
            )

        return SignalType.HOLD

    def _install_brackets(self, fill_price: float, side: str) -> None:
        """Arm take-profit / stop-loss levels for the freshly opened side."""
        if self._use_atr_stops:
            self._sl_price, self._tp_price = self._calculate_atr_stop_levels(fill_price, side)
            return
        if side == "long":
            self._tp_price = fill_price * (1 + self._own_params["tp_pct"])
            self._sl_price = fill_price * (1 - self._own_params["sl_pct"])
        else:
            self._tp_price = fill_price * (1 - self._own_params["tp_pct"])
            self._sl_price = fill_price * (1 + self._own_params["sl_pct"])

    def _clear_bracket_levels(self) -> None:
        """Retire live exit levels; runs on close-out and on reset alike."""
        self._tp_price = 0.0
        self._sl_price = 0.0

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        """Fill the order, then refresh (or drop) the bracket around it."""
        signal = super()._process_signal(event, signal_type)
        if signal is None:
            return signal

        if signal_type is SignalType.BUY:
            self._install_brackets(event.close, "long")
        elif signal_type is SignalType.SELL and not self.long_only and self._position == "flat":
            self._install_brackets(event.close, "short")
        elif signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
            self._clear_bracket_levels()

        return signal

    def reset(self) -> None:
        """Clear run history together with any live bracket levels."""
        super().reset()
        self._clear_bracket_levels()

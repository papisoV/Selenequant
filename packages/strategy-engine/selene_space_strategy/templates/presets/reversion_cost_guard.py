"""Mean-reversion preset with an explicit friction hurdle (A7).

Reversion trades die quietly when the snap-back is smaller than what the
broker and the tape charge for the round trip. This template prices that
death before it happens: at entry time it compares the expected reversion
against a budget of trading costs, and only fires when the edge survives.

Definitions
-----------
expected reversion  = |mean - close| / mean
                      how far price sits from its reference average,
                      expressed as the fraction one expects to reclaim.
friction budget     = round-trip cost. Either supplied directly through
                      ``friction_budget``, or derived as
                      ``effective_fee * 2 + slippage_bps * 2 / 1e4``
                      (a fee plus slippage on each of the two legs).
admission rule      = expected reversion strictly greater than
                      ``friction budget * min_edge_mult``.

Once admitted, the trade ends when price snaps back to the average, or
when the fixed / ATR-scaled bracket takes it out first.

Knob reference
--------------
ma_len            window of the mean-reversion reference (20)
ma_type           ``sma`` or ``ema`` reference line (sma)
friction_budget   explicit budget override; None derives from fees/slippage
min_edge_mult     multiplier demanding extra edge over bare costs (1.0)
tp_pct / sl_pct   fixed profit-target / stop-loss distances (5% / 3%)

Delivery note: templates/reversion/ ships YAML descriptors only (no
factory.py), so this A7 behaviour lands as a Python preset to join
STRATEGY_REGISTRY append-only while staying invisible to the compiler /
tuner path that consumes those YAML files.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.indicators.ema import EMA
from selene_space_strategy.indicators.sma import SMA
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class ReversionCostGuardStrategy(BacktestStrategyTemplate):
    """Entries priced against their own round-trip costs.

    Flat: compute the deviation from the reference average in both
    directions; BUY when the downward gap beats the (scaled) friction
    budget, SELL on the mirrored upward gap when shorts enabled. In-market:
    bracket levels first; then a close back at/beyond the average closes
    the position — the reversion has been harvested.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        ma_len: int = 20,
        ma_type: str = "sma",
        friction_budget: float | None = None,
        min_edge_mult: float = 1.0,
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
        super().__init__(
            symbol=symbol,
            initial_capital=initial_capital,
            leverage=leverage,
            fee_rate=fee_rate,
            max_bars=max_bars,
            long_only=long_only,
            use_atr_stops=use_atr_stops,
            atr_stop_period=atr_stop_period,
            atr_sl_mult=atr_sl_mult,
            atr_tp_mult=atr_tp_mult,
            **kwargs,
        )

        # Single mapping for every template-owned knob — normalised once at
        # storage time, replayed verbatim by get_parameters.
        self._own_params: dict[str, Any] = {
            "ma_len": ma_len,
            "ma_type": ma_type.lower(),
            "friction_budget": None if friction_budget is None else float(friction_budget),
            "min_edge_mult": float(min_edge_mult),
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

    def _reference_average(self, data: pd.DataFrame) -> pd.Series:
        """Directional anchor: EMA or SMA per ``ma_type``."""
        average_cls = {"ema": EMA}.get(self._own_params["ma_type"], SMA)
        return average_cls(self._own_params["ma_len"]).calculate(data)

    def _round_trip_friction(self) -> float:
        """Cost of one full round trip, either explicit or derived."""
        explicit = self._own_params["friction_budget"]
        if explicit is not None:
            return explicit
        per_leg = self._effective_fee_rate() + self.slippage_bps / 10_000.0
        return per_leg * 2.0

    def _edge_clears_hurdle(self, expected: float) -> bool:
        """True when the signed reversion expectation outruns the scaled budget."""
        return expected > self._round_trip_friction() * self._own_params["min_edge_mult"]

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Admit only reversions whose edge pays for the round trip."""
        knob = self._own_params
        if len(data) < knob["ma_len"] + 2:
            return SignalType.HOLD

        tip = data.iloc[-1]
        average_tip = self._reference_average(data).iloc[-1]
        if pd.isna(average_tip) or average_tip <= 0:
            return SignalType.HOLD

        if self._position == "flat":
            gap_down = (average_tip - tip.close) / average_tip  # long-side expectation
            gap_up = -gap_down                                  # short-side expectation
            if knob["enable_long"] and self._edge_clears_hurdle(gap_down):
                return SignalType.BUY
            if knob["enable_short"] and not self.long_only and self._edge_clears_hurdle(gap_up):
                return SignalType.SELL
            return SignalType.HOLD

        bracket_exit: SignalType | None = None
        if self._position == "long":
            if self._tp_price > 0 and tip.high >= self._tp_price:
                bracket_exit = SignalType.CLOSE_LONG
            elif self._sl_price > 0 and tip.low <= self._sl_price:
                bracket_exit = SignalType.CLOSE_LONG
            elif tip.close >= average_tip:
                bracket_exit = SignalType.SELL
        elif self._position == "short" and not self.long_only:
            if self._tp_price > 0 and tip.low <= self._tp_price:
                bracket_exit = SignalType.CLOSE_SHORT
            elif self._sl_price > 0 and tip.high >= self._sl_price:
                bracket_exit = SignalType.CLOSE_SHORT
            elif tip.close <= average_tip:
                bracket_exit = SignalType.CLOSE_SHORT

        return bracket_exit if bracket_exit is not None else SignalType.HOLD

    def _set_brackets_on_fill(self, fill_price: float, direction: str) -> None:
        """Attach exit levels: ATR-scaled when enabled, fixed otherwise."""
        if self._use_atr_stops:
            self._sl_price, self._tp_price = self._calculate_atr_stop_levels(fill_price, direction)
        elif direction == "long":
            self._tp_price = fill_price * (1 + self._own_params["tp_pct"])
            self._sl_price = fill_price * (1 - self._own_params["sl_pct"])
        else:
            self._tp_price = fill_price * (1 - self._own_params["tp_pct"])
            self._sl_price = fill_price * (1 + self._own_params["sl_pct"])

    def _clear_bracket_levels(self) -> None:
        """Retire live exit levels; shared by close-out handling and reset."""
        self._tp_price = 0.0
        self._sl_price = 0.0

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        """Delegate the fill upstream, then keep brackets in step with state."""
        signal = super()._process_signal(event, signal_type)
        if signal is None:
            return signal

        if signal_type is SignalType.BUY:
            self._set_brackets_on_fill(event.close, "long")
        elif signal_type is SignalType.SELL and not self.long_only and self._position == "flat":
            self._set_brackets_on_fill(event.close, "short")
        elif signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
            self._clear_bracket_levels()

        return signal

    def reset(self) -> None:
        """Drop run history alongside any live brackets."""
        super().reset()
        self._clear_bracket_levels()

"""K-line trend rider: stochastic source through an adaptive supertrend cage.

Pipeline overview:
    1. Pull Lane's %K out of the OHLC frame.
    2. Compress %K with a CMO-weighted adaptive average (see
       :meth:`STOCHSupertrendStrategy._cmo_blend`).
    3. Shift the compressed line onto a neutral axis with ``src_offset``.
    4. Re-run the adaptive average on the shifted line -> centre line.
    5. Grow percentage bands around the centre, letting them only tighten
       in the direction of the prevailing run (supertrend ratchet).
    6. Fold band + direction into the OTT trigger line.
    7. Trade crossings of price versus a lagged snapshot of the trigger.

Entries:
    long  : shifted source rises above the two-bars-back trigger while the
            one-bar-back source still sat under the three-bars-back trigger
    short : mirror image, crossing downward

Exits: fixed or ATR brackets, plus a hard close when the direction bias
flips against the open position.

Tuned for intraday charts (2m/5m/15m).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.stoch import Stochastic
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


# Width of the up/down momentum accumulator inside the adaptive blend.
_MOMENTUM_WINDOW = 9


def _signed_moves(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split bar-to-bar moves into separate positive and negative piles.

    Row 0 has no predecessor and contributes zeros to both sides.
    """
    steps = np.diff(values)
    upside = np.concatenate(([0.0], np.where(steps > 0.0, steps, 0.0)))
    downside = np.concatenate(([0.0], np.where(steps < 0.0, -steps, 0.0)))
    return upside, downside


class STOCHSupertrendStrategy(BacktestStrategyTemplate):
    """Adaptive supertrend driven by a stochastic-derived source.

    Chained construction: raw %K -> CMO-adaptive compression -> axis
    shift -> second adaptive pass forms the centre line -> ratcheting
    percentage bands -> direction fold -> OTT trigger. Entries read a
    deliberately lagged trigger pair so the template fires one bar later
    than a naive cross would.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        # STOCH parameters
        k_len: int = 14,
        k_smooth_len: int = 9,
        # MAvg parameters
        length: int = 2,
        # Band parameters
        percent: float = 2.0,
        # Source offset
        src_offset: float = 1000.0,
        # Exit parameters
        tp_pct: float = 0.05,
        sl_pct: float = 0.03,
        enable_long: bool = True,
        enable_short: bool = False,
        # Trend filter
        trend_filter_enabled: bool = False,
        adx_length: int = 14,
        adx_threshold: float = 25.0,
        # ATR dynamic stops
        use_atr_stops: bool = False,
        atr_stop_period: int = 14,
        atr_sl_mult: float = 2.0,
        atr_tp_mult: float = 3.0,
        # Template base params forwarded
        **kwargs,
    ) -> None:
        """Create the stochastic supertrend rider.

        Args:
            symbol: Trading pair.
            initial_capital: Starting capital.
            leverage: Leverage multiplier.
            fee_rate: Trading fee rate.
            max_bars: Maximum bars retained in the rolling window.
            long_only: When True, SELL only flattens an open long.
            k_len: Lookback of the underlying stochastic.
            k_smooth_len: Span of the adaptive compression on %K.
            length: Span of the second adaptive pass forming the centre.
            percent: Band width in percent of the centre line.
            src_offset: Constant lifting the compressed K off zero.
            tp_pct: Take-profit fraction of the fill price.
            sl_pct: Stop-loss fraction of the fill price.
            enable_long: Allow bullish entries.
            enable_short: Allow bearish entries (needs long_only off).
            trend_filter_enabled: Gate entries behind ADX strength.
            adx_length: ADX lookback for the regime gate.
            adx_threshold: Minimum ADX confirming a trending regime.
            use_atr_stops: Swap fixed brackets for ATR-scaled levels.
            atr_stop_period: ATR lookback for the scaled levels.
            atr_sl_mult: ATR multiple for the stop leg.
            atr_tp_mult: ATR multiple for the target leg.
        """
        super().__init__(
            symbol, initial_capital, leverage, fee_rate, max_bars, long_only,
            use_atr_stops=use_atr_stops,
            atr_stop_period=atr_stop_period,
            atr_sl_mult=atr_sl_mult,
            atr_tp_mult=atr_tp_mult,
            **kwargs,
        )

        self.k_len = k_len
        self.k_smooth_len = k_smooth_len
        self.length = length
        self.percent = percent
        self.src_offset = src_offset
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.enable_long = enable_long
        self.enable_short = enable_short
        self.trend_filter_enabled = trend_filter_enabled
        self.adx_length = adx_length
        self.adx_threshold = adx_threshold

        # Armed bracket levels; 0.0 means disarmed.
        self._tp_price: float = 0.0
        self._sl_price: float = 0.0

    def get_parameters(self) -> dict[str, Any]:
        merged = {
            **super().get_parameters(),
            "k_len": self.k_len,
            "k_smooth_len": self.k_smooth_len,
            "length": self.length,
            "percent": self.percent,
            "src_offset": self.src_offset,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "enable_long": self.enable_long,
            "enable_short": self.enable_short,
            "trend_filter_enabled": self.trend_filter_enabled,
            "adx_length": self.adx_length,
            "adx_threshold": self.adx_threshold,
        }
        return merged

    @staticmethod
    def _cmo_blend(source: pd.Series, span: int) -> pd.Series:
        """ Kaufman-style adaptive mean whose speed follows the CMO.

        Per bar, the net-to-gross move ratio over a fixed accumulation
        window (always 9 bars wide, independent of ``span``) yields a
        efficiency-like coefficient in [-1, 1]; its absolute value scales
        the base weight ``2 / (span + 1)``. Bars whose accumulator holds
        nothing (warm-up NaNs included) emit 0 rather than carrying the
        prior value forward -- reproducing the legacy behaviour exactly.
        """
        raw = source.to_numpy(dtype="float64")
        n_rows = len(raw)

        upside, downside = _signed_moves(raw)
        up_total = pd.Series(upside).rolling(_MOMENTUM_WINDOW).sum().to_numpy()
        down_total = pd.Series(downside).rolling(_MOMENTUM_WINDOW).sum().to_numpy()

        base_weight = 2.0 / (span + 1)
        blended = np.zeros(n_rows, dtype="float64")
        for pos in range(1, n_rows):
            gross = up_total[pos] + down_total[pos]
            if up_total[pos] > 0 or down_total[pos] > 0:
                net_ratio = (up_total[pos] - down_total[pos]) / gross if gross > 0 else 0.0
                speed = base_weight * abs(net_ratio)
                blended[pos] = speed * raw[pos] + (1.0 - speed) * blended[pos - 1]
            # else: row stays 0.0 -- matches the historical seeding rule

        return pd.Series(blended, index=source.index)

    def _build_lines(self, data: pd.DataFrame) -> dict[str, pd.Series]:
        """Assemble the centre line, ratcheted bands, bias and trigger.

        Returns a mapping with keys ``source``, ``centre``, ``floor``,
        ``ceil``, ``bias`` and ``trigger``.
        """
        n_rows = len(data)

        raw_k = Stochastic(self.k_len, 3, self.k_len).calculate(data)
        squeezed_k = self._cmo_blend(raw_k, self.k_smooth_len)
        source = squeezed_k + self.src_offset

        centre = self._cmo_blend(source, self.length)
        half_width = centre * self.percent * 0.01
        floor_vals = (centre - half_width).to_numpy(dtype="float64")
        ceil_vals = (centre + half_width).to_numpy(dtype="float64")
        centre_vals = centre.to_numpy(dtype="float64")

        # Ratchet: each band may only extend away from the centre line.
        for pos in range(1, n_rows):
            if centre_vals[pos] > floor_vals[pos - 1]:
                floor_vals[pos] = max(floor_vals[pos], floor_vals[pos - 1])
            if centre_vals[pos] < ceil_vals[pos - 1]:
                ceil_vals[pos] = min(ceil_vals[pos], ceil_vals[pos - 1])

        bias = np.ones(n_rows, dtype=int)
        for pos in range(1, n_rows):
            if bias[pos - 1] == -1 and centre_vals[pos] > ceil_vals[pos - 1]:
                bias[pos] = 1
            elif bias[pos - 1] == 1 and centre_vals[pos] < floor_vals[pos - 1]:
                bias[pos] = -1
            else:
                bias[pos] = bias[pos - 1]

        trigger = np.zeros(n_rows, dtype="float64")
        for pos in range(n_rows):
            active = floor_vals[pos] if bias[pos] == 1 else ceil_vals[pos]
            if centre_vals[pos] > active:
                trigger[pos] = active * (200 + self.percent) / 200
            else:
                trigger[pos] = active * (200 - self.percent) / 200

        idx = data.index
        return {
            "source": source,
            "centre": centre,
            "floor": pd.Series(floor_vals, index=idx),
            "ceil": pd.Series(ceil_vals, index=idx),
            "bias": pd.Series(bias, index=idx),
            "trigger": pd.Series(trigger, index=idx),
        }

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Decide the action for the newest bar from the trigger cross."""
        warmup = max(self.k_len, self.k_smooth_len) + 10
        if len(data) < warmup:
            return SignalType.HOLD

        lines = self._build_lines(data)
        tip = len(data) - 1

        src_now = lines["source"].iloc[tip]
        src_before = lines["source"].iloc[tip - 1]
        trig_lag2 = lines["trigger"].iloc[max(0, tip - 2)]
        trig_lag3 = lines["trigger"].iloc[max(0, tip - 3)]
        bias_now = lines["bias"].iloc[tip]
        peak = data["high"].iloc[tip]
        trough = data["low"].iloc[tip]

        if pd.isna(src_now) or pd.isna(trig_lag2):
            return SignalType.HOLD

        # === ENTRIES ===
        if self._position == "flat":
            if self.trend_filter_enabled and self.enable_long:
                trending, _ = self.check_trend_filter(
                    data, self.adx_length, self.adx_threshold, direction="long"
                )
                if not trending:
                    return SignalType.HOLD

            if self.enable_long and src_now > trig_lag2 and src_before <= trig_lag3:
                return SignalType.BUY

            if self.enable_short and not self.long_only:
                if self.trend_filter_enabled:
                    trending, _ = self.check_trend_filter(
                        data, self.adx_length, self.adx_threshold, direction="short"
                    )
                    if not trending:
                        return SignalType.HOLD
                if src_now < trig_lag2 and src_before >= trig_lag3:
                    return SignalType.SELL

        # === EXITS ===
        elif self._position == "long":
            if self._tp_price > 0 and peak >= self._tp_price:
                return SignalType.CLOSE_LONG
            if self._sl_price > 0 and trough <= self._sl_price:
                return SignalType.CLOSE_LONG
            if bias_now == -1:
                return SignalType.CLOSE_LONG

        elif self._position == "short" and not self.long_only:
            if self._tp_price > 0 and trough <= self._tp_price:
                return SignalType.CLOSE_SHORT
            if self._sl_price > 0 and peak >= self._sl_price:
                return SignalType.CLOSE_SHORT
            if bias_now == 1:
                return SignalType.CLOSE_SHORT

        return SignalType.HOLD

    def _arm_brackets(self, fill_price: float, side: str) -> None:
        """Fix the exit levels for a freshly opened position."""
        if self._use_atr_stops:
            self._sl_price, self._tp_price = self._calculate_atr_stop_levels(
                fill_price, side
            )
        elif side == "long":
            self._tp_price = fill_price * (1 + self.tp_pct)
            self._sl_price = fill_price * (1 - self.sl_pct)
        else:
            self._tp_price = fill_price * (1 - self.tp_pct)
            self._sl_price = fill_price * (1 + self.sl_pct)

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        """Dispatch through the base, then arm or clear bracket levels."""
        dispatched = super()._process_signal(event, signal_type)
        if dispatched is None:
            return None

        if signal_type == SignalType.BUY:
            self._arm_brackets(event.close, "long")
        elif signal_type == SignalType.SELL and not self.long_only and self._position == "flat":
            self._arm_brackets(event.close, "short")
        elif signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
            self._tp_price = 0.0
            self._sl_price = 0.0

        return dispatched

    def reset(self) -> None:
        """Drop armed levels along with the base state."""
        super().reset()
        self._tp_price = 0.0
        self._sl_price = 0.0

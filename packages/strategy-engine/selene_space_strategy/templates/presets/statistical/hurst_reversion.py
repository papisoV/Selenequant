"""Hurst-gated mean-reversion template.

A rolling rescaled-range exponent under 0.5 is the fingerprint of a
series that snaps back toward its mean instead of drifting. This
template treats that reading as a permit: only while the exponent sits
below ``hurst_threshold`` may positions be opened or closed at all.
The permit alone does not trade -- an RSI extreme picks the instant
(below ``rsi_entry`` buys the panic, above ``rsi_exit`` sells the
euphoria), and a later settle into a neutral band closes the book on
the assumption the mean has been revisited.

Both statistics share one ``lookback`` window: max-lag for the
exponent, averaging span for the oscillator.
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType

# RSI readings inside this band are treated as "mean revisited".
# The close branch emits CLOSE_LONG even while short -- historical
# quirk preserved verbatim under the behavioural lock.
_NEUTRAL_FLOOR = 40
_NEUTRAL_CEILING = 60


class HurstReversionStrategy(BacktestStrategyTemplate):
    """Fade RSI extremes only when the regime test certifies reversion."""

    def __init__(
        self,
        symbol: str,
        hurst_threshold: float = 0.45,
        rsi_entry: int = 30,
        rsi_exit: int = 70,
        lookback: int = 20,
        **kwargs,
    ) -> None:
        super().__init__(symbol=symbol, **kwargs)
        self.hurst_threshold = hurst_threshold
        self.rsi_entry = rsi_entry
        self.rsi_exit = rsi_exit
        self.lookback = lookback

    def _regime_permits(self, data: pd.DataFrame) -> bool:
        """True while the Hurst exponent certifies a mean-reverting tape."""
        exponent = self.compute("hurst", data, max_lag=self.lookback).last()
        return exponent is not None and exponent < self.hurst_threshold

    @staticmethod
    def _read_tape(reading: float, entry_level: int, exit_level: int) -> SignalType:
        """Map one oscillator reading to an action, extremes first."""
        if reading < entry_level:
            return SignalType.BUY
        if reading > exit_level:
            return SignalType.SELL
        if _NEUTRAL_FLOOR <= reading <= _NEUTRAL_CEILING:
            # Settled back to fair value -> flatten.
            return SignalType.CLOSE_LONG
        return SignalType.HOLD

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Regime permit first; oscillator timing only once permitted."""
        if not self._regime_permits(data):
            return SignalType.HOLD

        reading = self.compute("rsi", data, period=self.lookback).last()
        if reading is None:
            return SignalType.HOLD

        return self._read_tape(reading, self.rsi_entry, self.rsi_exit)

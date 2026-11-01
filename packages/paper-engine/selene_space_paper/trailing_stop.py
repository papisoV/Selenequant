"""Trailing stop tracker for paper trading.

Stateful, ratcheting stop-loss that follows the peak price of an open
position. When current price breaches the trailing level (drawdown
from peak exceeds ``trail_drop`` after the position has gained at
least ``activation_gain``), a SELL signal is emitted to close the
position.

Distinct from ``selene_space_risk.dex_rules.TrailingStopRule``, which
is a passive alert that depends on an externally-tracked
``highest_price``. This module owns the peak-price state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


@dataclass
class TrailingStopConfig:
    """Configuration for the trailing stop tracker."""

    # Activate trailing only after the position has gained this much
    # from entry. Before activation, the stop stays at the fixed
    # initial_stop_pct below entry.
    activation_gain: float = 0.05
    # Once activated, trail the peak price by this fraction.
    trail_drop: float = 0.03
    # Initial stop-loss as a fraction below entry (used pre-activation).
    initial_stop_pct: float = 0.05
    # Minimum interval (seconds) between SELL emissions for the same
    # symbol — prevents spamming SELL signals on a choppy cross.
    min_emit_interval_seconds: float = 30.0


@dataclass
class _PositionPeak:
    """Mutable per-symbol peak tracker.

    Fields:
        entry_price: Anchor entry price (set when position opens).
        peak_price: Highest price seen since activation; ratchets up.
        activated: True once activation_gain reached; trailing engages.
        last_emit_epoch: Last time a SELL was emitted (throttle).
    """

    entry_price: float
    peak_price: float
    activated: bool = False
    last_emit_epoch: float = 0.0


@dataclass
class TrailingStopTracker:
    """Owns per-symbol peak-price state and emits SELL signals.

    Usage in a pipeline:
        tracker = TrailingStopTracker(config)
        # On every price tick:
        sig = tracker.update(symbol, current_price, position_quantity)
        if sig is not None:
            pipeline.process_signal(sig)  # emits SELL
        # On position close:
        tracker.clear(symbol)
    """

    config: TrailingStopConfig = field(default_factory=TrailingStopConfig)
    _peaks: dict[str, _PositionPeak] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open_position(self, symbol: str, entry_price: float) -> None:
        """Register a new long position. Resets any prior peak state.

        Idempotent — calling twice with the same entry just refreshes
        the anchor. Call this when a BUY is filled.
        """
        self._peaks[symbol] = _PositionPeak(
            entry_price=entry_price,
            peak_price=entry_price,
        )

    def clear(self, symbol: str) -> None:
        """Drop all tracking state for a symbol (position closed)."""
        self._peaks.pop(symbol, None)

    def update(
        self,
        symbol: str,
        current_price: float,
        position_quantity: float,
        *,
        now_epoch: float | None = None,
    ) -> Signal | None:
        """Push a new price; return a SELL Signal if trailing stop hit.

        Returns None when:
            - no open position is tracked for ``symbol``
            - current_price <= 0 (bad tick)
            - trailing not yet activated
            - drawdown from peak has not exceeded trail_drop
            - SELL was emitted too recently (throttle)

        Args:
            symbol: Trading pair.
            current_price: Latest mark / last price.
            position_quantity: Current long position size. Must be > 0
                to warrant a SELL; 0 means position was closed elsewhere.
            now_epoch: Optional override for testability; defaults to
                time.time().
        """
        peak = self._peaks.get(symbol)
        if peak is None:
            return None
        if current_price <= 0:
            return None
        if position_quantity <= 0:
            # Position closed elsewhere; clear our state and bail.
            self.clear(symbol)
            return None

        now = now_epoch if now_epoch is not None else time.time()

        # Ratchet peak up (never down).
        if current_price > peak.peak_price:
            peak.peak_price = current_price

        # Check activation: gain from entry must exceed activation_gain.
        gain = (peak.peak_price - peak.entry_price) / peak.entry_price
        if not peak.activated:
            if gain >= self.config.activation_gain:
                peak.activated = True
            else:
                # Pre-activation: use fixed initial stop below entry.
                stop_level = peak.entry_price * (1.0 - self.config.initial_stop_pct)
                if current_price <= stop_level:
                    return self._emit_sell(
                        symbol, current_price, position_quantity, now,
                        reason=(
                            f"initial_stop: {symbol} @ {current_price} "
                            f"(entry={peak.entry_price}, stop={stop_level:.2f})"
                        ),
                    )
                return None

        # Post-activation: trail peak by trail_drop.
        stop_level = peak.peak_price * (1.0 - self.config.trail_drop)
        if current_price <= stop_level:
            drawdown = (peak.peak_price - current_price) / peak.peak_price
            return self._emit_sell(
                symbol, current_price, position_quantity, now,
                reason=(
                    f"trailing_stop: {symbol} @ {current_price} "
                    f"(peak={peak.peak_price}, stop={stop_level:.2f}, "
                    f"drawdown={drawdown:.2%}, gain_at_peak={gain:.2%})"
                ),
            )
        return None

    def status(self, symbol: str) -> dict[str, Any] | None:
        """Snapshot of the tracker state for a symbol (for monitoring)."""
        peak = self._peaks.get(symbol)
        if peak is None:
            return None
        return {
            "symbol": symbol,
            "entry_price": peak.entry_price,
            "peak_price": peak.peak_price,
            "activated": peak.activated,
            "current_trailing_stop": (
                peak.peak_price * (1.0 - self.config.trail_drop)
                if peak.activated
                else peak.entry_price * (1.0 - self.config.initial_stop_pct)
            ),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _emit_sell(
        self,
        symbol: str,
        current_price: float,
        position_quantity: float,
        now_epoch: float,
        *,
        reason: str,
    ) -> Signal | None:
        """Build a SELL Signal, respecting the emit-throttle window."""
        peak = self._peaks.get(symbol)
        if peak is None:
            return None
        if now_epoch - peak.last_emit_epoch < self.config.min_emit_interval_seconds:
            return None
        peak.last_emit_epoch = now_epoch
        return Signal(
            strategy_id="trailing_stop",
            symbol=symbol,
            side=OrderSide.SELL,
            strength=1.0,
            timestamp=int(now_epoch * 1000),
            reason=reason,
            quantity=position_quantity,
        )

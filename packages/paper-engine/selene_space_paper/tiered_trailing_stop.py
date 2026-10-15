"""Tiered trailing stop tracker — four-tier ratcheting stop-loss.

A four-tier state machine that progressively tightens the trailing
stop as profit grows.

Tiers (ratchet only ascends; never descends):
  - NONE       : below 1.5% profit — uses hard_stop_pct (-2%)
  - T1_PROTECT : >= 1.5% profit   — trail by 50% drop from peak
  - T2_LOCK    : >= 3.0% profit   — trail by 30% drop from peak
  - T3_TRAIL   : >= 6.0% profit   — trail by 20% drop from peak

Distinction from ``trailing_stop.py`` (the existing two-tier tracker):
  - four tiers vs two (initial + trailing)
  - ratchet-only tier promotion (never downgrades)
  - fill-event driven (``on_fill``) rather than relying solely on ticks
  - state is a ``@dataclass(frozen=True)`` value object; every
    mutation returns a fresh instance via ``dataclasses.replace``

v4 Mi3: public methods are ``on_tick`` and ``on_fill`` (NOT ``update``)
to align naming with the tick/fill event taxonomy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Optional

from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


class Tier(str, Enum):
    """Four-tier profit-protection state machine — ratchets up only."""

    NONE = "none"
    T1_PROTECT = "t1_protect"
    T2_LOCK = "t2_lock"
    T3_TRAIL = "t3_trail"


@dataclass(frozen=True)
class TieredTrailingConfig:
    """Immutable configuration for the tiered trailing-stop tracker.

    Fields are profit thresholds (as fractions of entry_price) and the
    corresponding drop-from-peak fractions used to compute the stop level
    at each tier. The hard_stop_pct is the unconditional floor used
    pre-activation (Tier.NONE).
    """

    # Absolute floor: stop this far below entry when no tier is active.
    hard_stop_pct: float = 0.02
    # Profit threshold to enter T1_PROTECT (1.5%).
    low_tier_threshold: float = 0.015
    # Profit threshold to enter T2_LOCK (3.0%).
    first_tier_threshold: float = 0.03
    # Profit threshold to enter T3_TRAIL (6.0%).
    second_tier_threshold: float = 0.06
    # Drop from peak when T1_PROTECT is active.
    low_tier_drop_pct: float = 0.5
    # Drop from peak when T2_LOCK is active.
    first_tier_drop_pct: float = 0.30
    # Drop from peak when T3_TRAIL is active.
    second_tier_drop_pct: float = 0.20
    # Minimum interval (seconds) between SELL emissions for the same
    # symbol — prevents spamming on a choppy cross.
    min_emit_interval_seconds: float = 30.0


@dataclass(frozen=True)
class LiquidityContext:
    """Precomputed liquidity structure passed in by the caller.

    The tracker runs on a microsecond-level hot path (sync fill/tick
    loops), so it must never compute a volume profile itself — the
    caller (e.g. local-api wiring) extracts HVN prices from the VPVR
    aggregator ahead of time and hands over this lightweight snapshot.

    Fields:
        hvn_prices: High-volume-node price levels (from VPVR levels
            with volume >= a POC-fraction threshold), nearest below
            current price first.
        round_step: Granularity of obvious round-number levels (e.g.
            1000.0 for BTC); 0 disables round-level avoidance.
        atr: Current ATR in price units; buffer width per level is
            ``max(atr * buffer_atr, min_buffer_pct * price)``. When
            ``atr`` is 0 the fallback is the pct floor alone.
        buffer_atr: ATR multiple used for the stop-hunt buffer width.
        min_buffer_pct: Minimum buffer width as a fraction of price.
    """

    hvn_prices: tuple[float, ...] = ()
    round_step: float = 0.0
    atr: float = 0.0
    buffer_atr: float = 0.25
    min_buffer_pct: float = 0.001


@dataclass(frozen=True)
class TieredTrailingStopState:
    """Immutable per-symbol snapshot of the tiered trailing tracker.

    Every ``on_fill`` / ``on_tick`` mutation returns a fresh instance via
    ``dataclasses.replace`` — never mutates in place. This honors the
    project's immutability rule and makes the state safe to publish to
    observers (e.g. monitoring) without defensive copies.
    """

    symbol: str
    entry_price: float
    peak_price: float
    last_quantity: float
    current_tier: Tier = Tier.NONE
    closed: bool = False
    last_emit_epoch: float = 0.0
    # Last effective stop computed in on_tick (liquidity nudge applied).
    # None until the first on_tick; status() reports it when present and
    # falls back to the raw tier stop otherwise.
    effective_stop: float | None = None


class TieredTrailingStopTracker:
    """Owns per-symbol tier state and emits SELL signals on close.

    Usage in a pipeline:
        tracker = TieredTrailingStopTracker(config)
        # On BUY fill:
        tracker.on_fill(symbol, fill_qty, fill_price)
        # On every price tick:
        state = tracker.on_tick(symbol, tick_price, position_quantity)
        if state is not None and state.closed:
            pipeline.process_signal(tracker.build_sell_signal(state))
        # On SELL fill (position closed):
        tracker.clear(symbol)
    """

    def __init__(self, config: TieredTrailingConfig | None = None) -> None:
        self._config = config or TieredTrailingConfig()
        self._peaks: dict[str, TieredTrailingStopState] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_fill(
        self,
        symbol: str,
        fill_qty: float,
        fill_price: float,
    ) -> Optional[TieredTrailingStopState]:
        """Process a fill event — pure synchronous, no I/O, no blocking.

        Called from ``paper_execution.py``'s fill-callback for-loop (L543),
        which is a tight sync loop — must return in microseconds.

        Semantics:
            - First BUY fill on a symbol with no prior state: initialize
              entry/peak at fill_price.
            - Subsequent BUY with qty greater than last seen qty: treat
              as add-on; re-anchor entry/peak at new fill_price and reset
              tier to NONE (re-arm protection).
            - Subsequent fill with qty <= last seen qty: treat as partial
              close / no-op for tier logic; just refresh last_quantity.
            - SELL fill (qty < 0 or symbol going flat) is handled by
              ``clear`` — callers MUST invoke ``clear(symbol)`` on the
              SELL fill event (see ``notify_fill_tiered`` in the pipeline).

        Returns the new state (never None when initializing; returns the
        updated snapshot otherwise). Returns None only if the symbol's
        state was already closed (caller should have cleared it).
        """
        state = self._peaks.get(symbol)
        if state is None:
            new_state = TieredTrailingStopState(
                symbol=symbol,
                entry_price=fill_price,
                peak_price=fill_price,
                last_quantity=fill_qty,
                current_tier=Tier.NONE,
                closed=False,
            )
        elif state.closed:
            # Position was already closed; do not resurrect stale state.
            return state
        elif fill_qty > state.last_quantity:
            # Add-on (pyramid): re-anchor entry+peak, reset tier.
            new_state = replace(
                state,
                entry_price=fill_price,
                peak_price=fill_price,
                last_quantity=fill_qty,
                current_tier=Tier.NONE,
                closed=False,
                effective_stop=None,
            )
        else:
            # Partial close or unchanged: just refresh last_quantity.
            new_state = replace(state, last_quantity=fill_qty)
        self._peaks[symbol] = new_state
        return new_state

    def on_tick(
        self,
        symbol: str,
        tick_price: float,
        position_quantity: float,
        *,
        now_epoch: float | None = None,
        liquidity: LiquidityContext | None = None,
    ) -> Optional[TieredTrailingStopState]:
        """Process a tick event — pure synchronous, no I/O, no blocking.

        Updates peak (ratchet up only), recomputes tier (ratchet up only),
        computes the stop level for the current tier, and marks ``closed``
        if the tick breaches the stop or the hard-stop floor.

        Args:
            symbol: Trading pair.
            tick_price: Latest mark / last price.
            position_quantity: Current long position size. 0 means the
                position was closed elsewhere; we clear our state and
                return None.
            now_epoch: Optional override for testability; defaults to
                ``time.time()``. Used only for the SELL emit-throttle.
            liquidity: Optional precomputed liquidity structure. When
                given, the stop level is nudged below obvious stop-hunt
                zones (high-volume nodes / round numbers). ``None``
                keeps the legacy pure peak-pct behavior exactly.

        Returns the updated state, or None if no position is tracked /
        position went flat / bad tick. Callers should emit a SELL when
        the returned state has ``closed=True`` (see ``build_sell_signal``).
        """
        state = self._peaks.get(symbol)
        if state is None or state.closed:
            return None
        if tick_price <= 0:
            return None
        if position_quantity <= 0:
            # Position closed elsewhere — drop our state.
            self.clear(symbol)
            return None

        # Ratchet peak up (never down).
        new_peak = max(state.peak_price, tick_price)
        profit_pct = (new_peak - state.entry_price) / state.entry_price

        # Ratchet tier up (never down).
        new_tier = self._compute_tier(profit_pct, state.current_tier)

        # Compute stop level for current tier (liquidity-aware nudge optional).
        stop_level = self._compute_stop_level(new_peak, profit_pct, new_tier)
        if liquidity is not None:
            stop_level = self._avoid_stop_hunt_zones(stop_level, liquidity)

        # Closed if tick breaches stop OR hard-stop floor (absolute loss).
        closed = tick_price <= stop_level or profit_pct <= -self._config.hard_stop_pct

        # now_epoch is accepted for testability parity with build_sell_signal
        # but not consumed here — on_tick does not throttle, it only ratchets.
        del now_epoch
        new_state = replace(
            state,
            peak_price=new_peak,
            current_tier=new_tier,
            closed=closed,
            effective_stop=stop_level,
        )
        self._peaks[symbol] = new_state
        return new_state

    def clear(self, symbol: str) -> None:
        """Drop all tracking state for a symbol (position closed)."""
        self._peaks.pop(symbol, None)

    def symbols(self) -> list[str]:
        """All symbols currently being tracked."""
        return list(self._peaks)

    def status(self, symbol: str) -> dict[str, Any] | None:
        """Snapshot of the tracker state for a symbol (for monitoring)."""
        state = self._peaks.get(symbol)
        if state is None:
            return None
        profit_pct = (state.peak_price - state.entry_price) / state.entry_price
        stop_level = (
            state.effective_stop
            if state.effective_stop is not None
            else self._compute_stop_level(
                state.peak_price, profit_pct, state.current_tier
            )
        )
        return {
            "symbol": state.symbol,
            "entry_price": state.entry_price,
            "peak_price": state.peak_price,
            "current_tier": state.current_tier.value,
            "profit_pct": profit_pct,
            "closed": state.closed,
            "stop_level": stop_level,
        }

    def build_sell_signal(
        self,
        state: TieredTrailingStopState,
        *,
        position_quantity: float,
        now_epoch: float | None = None,
    ) -> Optional[Signal]:
        """Build a SELL Signal from a closed state, honoring emit throttle.

        Returns None if the state is not closed, or if a SELL was emitted
        too recently for this symbol (throttle).
        """
        if not state.closed:
            return None
        now = now_epoch if now_epoch is not None else time.time()
        if now - state.last_emit_epoch < self._config.min_emit_interval_seconds:
            return None
        # Mutate the stored state's last_emit_epoch via replace.
        updated = replace(state, last_emit_epoch=now)
        self._peaks[state.symbol] = updated
        profit_pct = (state.peak_price - state.entry_price) / state.entry_price
        stop_level = (
            state.effective_stop
            if state.effective_stop is not None
            else self._compute_stop_level(
                state.peak_price, profit_pct, state.current_tier
            )
        )
        reason = (
            f"tiered_trailing_stop: {state.symbol} @ tier={state.current_tier.value} "
            f"(entry={state.entry_price}, peak={state.peak_price}, "
            f"stop={stop_level:.2f}, profit={profit_pct:.2%})"
        )
        return Signal(
            strategy_id="tiered_trailing_stop",
            symbol=state.symbol,
            side=OrderSide.SELL,
            strength=1.0,
            timestamp=int(now * 1000),
            reason=reason,
            quantity=position_quantity,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compute_tier(self, profit_pct: float, current: Tier) -> Tier:
        """Ratchet tier up only — never downgrades."""
        if profit_pct >= self._config.second_tier_threshold:
            return Tier.T3_TRAIL
        if profit_pct >= self._config.first_tier_threshold:
            if current == Tier.NONE:
                return Tier.T2_LOCK
            return max(current, Tier.T2_LOCK)
        if profit_pct >= self._config.low_tier_threshold:
            if current == Tier.NONE:
                return Tier.T1_PROTECT
            return max(current, Tier.T1_PROTECT)
        return current

    def _compute_stop_level(
        self,
        peak: float,
        profit_pct: float,
        tier: Tier,
    ) -> float:
        """Stop level for the given tier; falls back to hard_stop below entry."""
        if tier == Tier.T3_TRAIL:
            return peak * (1.0 - self._config.second_tier_drop_pct)
        if tier == Tier.T2_LOCK:
            return peak * (1.0 - self._config.first_tier_drop_pct)
        if tier == Tier.T1_PROTECT:
            return peak * (1.0 - self._config.low_tier_drop_pct)
        # Tier.NONE: use hard_stop_pct below entry (not below peak —
        # peak == entry when NONE is active, so they coincide at init).
        return peak * (1.0 - self._config.hard_stop_pct)

    def _avoid_stop_hunt_zones(
        self,
        stop: float,
        liquidity: LiquidityContext,
    ) -> float:
        """Nudge the stop below obvious stop-hunt zones.

        Stop orders habitually cluster just beneath obvious levels
        (high-volume nodes, round numbers); large players push price
        into those pockets to fill cheaply. If the candidate stop sits
        inside such a pocket (zone level >= stop > level - buffer), it
        is moved to the pocket's lower edge. Multiple pockets are
        resolved in one downward sweep, so the result never lands in
        another pocket.

        Buffer width per level: ``max(atr * buffer_atr,
        min_buffer_pct * level)`` — atr-scaled when available, with a
        pct floor. Zero when the context carries no structure.
        """
        zones = list(liquidity.hvn_prices)
        if liquidity.round_step > 0 and stop > liquidity.round_step:
            zones.append(round(stop / liquidity.round_step) * liquidity.round_step)
        if not zones:
            return stop

        # One downward sweep: while the stop sits in any pocket, drop it
        # to that pocket's lower edge. Bounded iterations guard against
        # pathological overlapping-zone inputs.
        for _ in range(len(zones) + 1):
            landed = None
            for level in zones:
                width = max(
                    liquidity.atr * liquidity.buffer_atr,
                    liquidity.min_buffer_pct * level,
                )
                if width <= 0:
                    continue
                if level >= stop > level - width:
                    landed = level
                    break
            if landed is None:
                return stop
            width = max(
                liquidity.atr * liquidity.buffer_atr,
                liquidity.min_buffer_pct * landed,
            )
            stop = landed - width
        return stop

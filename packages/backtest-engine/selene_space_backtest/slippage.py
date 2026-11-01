"""Slippage models — the single canonical implementation for the platform.

All slippage models implement the ``SlippageModel`` Protocol: one ``apply``
method carrying optional volume context (order notional + 24h reference
volume). Simple models ignore the context; volume-aware models use it.

This package sits at the bottom of the layering (depends only on
shared-types), so paper/live execution layers re-export ``RealisticSlippage``
from here instead of keeping their own copy. Historically the model was
duplicated across backtest-engine and paper-engine and held in lockstep by
an alignment test; the duplication is gone — there is exactly one
implementation, so backtest fill prices match paper/live fills exactly by
construction.
"""

from __future__ import annotations

from typing import Protocol

from selene_space_types.trading import OrderSide


class SlippageModel(Protocol):
    """Unified slippage interface (shared by backtest, paper, and shadow engines).

    ``apply`` adjusts the raw price to simulate market impact / slippage.
    Volume-aware models read ``order_notional`` / ``avg_24h_volume``;
    models without volume-aware logic simply ignore them, so callers can
    pass the context unconditionally through a single code path.
    """

    def apply(
        self,
        price: float,
        side: OrderSide,
        *,
        order_notional: float = 0.0,
        avg_24h_volume: float = 0.0,
    ) -> float:
        """Return the price after applying slippage.

        Args:
            price: Raw execution price.
            side: Order direction (buys slip up, sells slip down).
            order_notional: Remaining order size in quote currency.
            avg_24h_volume: 24h quote volume used for participation impact.
        """
        ...


class NoSlippage:
    """Zero slippage — fills at the exact price."""

    def apply(
        self,
        price: float,
        side: OrderSide,
        *,
        order_notional: float = 0.0,
        avg_24h_volume: float = 0.0,
    ) -> float:
        return price


class FixedSlippage:
    """Fixed slippage in basis points.

    1 basis point = 0.01%.  Default is 1 bp.

    For a BUY, the fill price is *higher* than the reference price.
    For a SELL, the fill price is *lower*.

    Args:
        bps: Slippage in basis points (default 1).
    """

    def __init__(self, bps: float = 1.0) -> None:
        if bps < 0:
            raise ValueError("Slippage basis points must be non-negative.")
        self._fraction = bps / 10_000.0

    def apply(
        self,
        price: float,
        side: OrderSide,
        *,
        order_notional: float = 0.0,
        avg_24h_volume: float = 0.0,
    ) -> float:
        if side == OrderSide.BUY:
            return price * (1.0 + self._fraction)
        return price * (1.0 - self._fraction)


class BidAskSpreadModel:
    """Bid-ask spread cost model — the primary execution cost in crypto.

    Buys fill at ask (higher), sells fill at bid (lower). The half-spread
    is applied on top of any other slippage model.

    Supports per-symbol spread configuration via liquidity tiers:
      - tier 1 (BTC, ETH): ~1-2 bps spread
      - tier 2 (major alts): ~3-5 bps spread
      - tier 3 (small caps): ~10-50 bps spread

    Note: this is a *spread* model, not a full slippage model — compose it
    with ``CompositeSlippage`` before injecting into engines.

    Args:
        default_spread_bps: Default half-spread in basis points (default 5).
        symbol_spread_bps: Per-symbol override, e.g. {"BTC-USDT": 1.0, "DOGE-USDT": 15.0}.
    """

    # Common liquidity tier presets
    TIER1_SPREAD_BPS = 1.5  # BTC, ETH
    TIER2_SPREAD_BPS = 5.0  # SOL, AVAX, etc.
    TIER3_SPREAD_BPS = 20.0  # Small caps, meme coins

    def __init__(
        self,
        default_spread_bps: float = 5.0,
        symbol_spread_bps: dict[str, float] | None = None,
    ) -> None:
        if default_spread_bps < 0:
            raise ValueError("default_spread_bps must be non-negative.")
        self._default_bps = default_spread_bps
        self._symbol_bps: dict[str, float] = symbol_spread_bps or {}

    def get_half_spread_bps(self, symbol: str) -> float:
        """Get half-spread in bps for a symbol."""
        return self._symbol_bps.get(symbol, self._default_bps)

    def apply(
        self,
        price: float,
        side: OrderSide,
        symbol: str = "",
        *,
        order_notional: float = 0.0,
        avg_24h_volume: float = 0.0,
    ) -> float:
        """Apply half-spread cost to fill price.

        Buys pay the ask (price + half_spread), sells receive the bid (price - half_spread).

        Args:
            price: Mid price.
            side: Order direction.
            symbol: Trading pair for per-symbol spread lookup.
            order_notional: Unused (accepted for protocol compatibility).
            avg_24h_volume: Unused (accepted for protocol compatibility).
        """
        half_spread_bps = self.get_half_spread_bps(symbol)
        fraction = half_spread_bps / 10_000.0
        if side == OrderSide.BUY:
            return price * (1.0 + fraction)
        return price * (1.0 - fraction)

    def apply_with_spread_data(
        self,
        price: float,
        side: OrderSide,
        bid: float | None = None,
        ask: float | None = None,
    ) -> float:
        """Apply spread using actual bid/ask data when available.

        Falls back to bps-based calculation if bid/ask not provided.

        Args:
            price: Mid price (used as fallback).
            side: Order direction.
            bid: Actual bid price (optional).
            ask: Actual ask price (optional).
        """
        if side == OrderSide.BUY and ask is not None and ask > 0:
            return ask
        if side == OrderSide.SELL and bid is not None and bid > 0:
            return bid
        return self.apply(price, side)


class CompositeSlippage:
    """Combine a slippage model with a spread model.

    Applies slippage first (market impact), then spread (bid-ask cost).
    This is the most realistic model for crypto backtesting.

    Args:
        slippage: Market impact slippage model.
        spread: Bid-ask spread model.
    """

    def __init__(self, slippage: SlippageModel, spread: BidAskSpreadModel) -> None:
        self._slippage = slippage
        self._spread = spread

    def apply(
        self,
        price: float,
        side: OrderSide,
        symbol: str = "",
        *,
        order_notional: float = 0.0,
        avg_24h_volume: float = 0.0,
    ) -> float:
        """Apply slippage (with volume context forwarded) then spread."""
        after_slippage = self._slippage.apply(
            price,
            side,
            order_notional=order_notional,
            avg_24h_volume=avg_24h_volume,
        )
        return self._spread.apply(after_slippage, side, symbol)


class VolumeSlippage:
    """Volume-dependent slippage — larger orders relative to bar volume slip more.

    Models market impact: a 10K order on a 100K volume bar slips more than
    the same order on a 10M volume bar.

    Without volume info the model falls back to ``default_bps``.

    Args:
        volume_impact_factor: Scaling factor for volume impact (default 0.1).
            Higher = more slippage per unit of volume participation.
        default_bps: Fallback bps when volume info is not provided.
        max_bps: Cap on slippage in basis points (default 100 = 1%).
    """

    def __init__(
        self,
        volume_impact_factor: float = 0.1,
        default_bps: float = 5.0,
        max_bps: float = 100.0,
    ) -> None:
        if volume_impact_factor < 0:
            raise ValueError("volume_impact_factor must be non-negative.")
        if default_bps < 0:
            raise ValueError("default_bps must be non-negative.")
        if max_bps < 0:
            raise ValueError("max_bps must be non-negative.")
        self._factor = volume_impact_factor
        self._default_bps = default_bps
        self._max_bps = max_bps

    def apply(
        self,
        price: float,
        side: OrderSide,
        *,
        order_notional: float = 0.0,
        avg_24h_volume: float = 0.0,
    ) -> float:
        """Apply volume-dependent slippage; flat ``default_bps`` when volume is absent."""
        if avg_24h_volume <= 0 or order_notional <= 0:
            fraction = self._default_bps / 10_000.0
            if side == OrderSide.BUY:
                return price * (1.0 + fraction)
            return price * (1.0 - fraction)

        volume_ratio = order_notional / avg_24h_volume
        slippage_bps = min(self._factor * volume_ratio * 10_000.0, self._max_bps)
        fraction = slippage_bps / 10_000.0

        if side == OrderSide.BUY:
            return price * (1.0 + fraction)
        return price * (1.0 - fraction)


class RealisticSlippage:
    """Volume-aware slippage — the canonical model shared with paper/live.

    Base slippage 3bp (realistic for crypto spot) plus a participation-rate
    term proportional to ``order_notional / avg_24h_volume``, capped at
    ``MAX_BPS`` so pathological participation cannot produce absurd fills.

    When ``avg_24h_volume`` or ``order_notional`` is unavailable (0.0), the
    model degrades to the base 3bp — still more conservative than the legacy
    1bp ``FixedSlippage`` default.

    Args:
        base_bps: Base slippage in basis points (default 3.0).
        volume_impact_factor: Scaling factor for participation-rate impact
            (default 0.1).
    """

    MAX_BPS = 200.0

    def __init__(
        self, base_bps: float = 3.0, volume_impact_factor: float = 0.1
    ) -> None:
        if base_bps < 0:
            raise ValueError("base_bps must be non-negative.")
        if volume_impact_factor < 0:
            raise ValueError("volume_impact_factor must be non-negative.")
        self._base_fraction = base_bps / 10_000.0
        self._impact_factor = volume_impact_factor

    def apply(
        self,
        price: float,
        side: OrderSide,
        *,
        order_notional: float = 0.0,
        avg_24h_volume: float = 0.0,
    ) -> float:
        fraction = self._base_fraction
        if avg_24h_volume > 0 and order_notional > 0:
            participation = order_notional / avg_24h_volume
            fraction += self._impact_factor * participation
            fraction = min(fraction, self.MAX_BPS / 10_000.0)
        slip = price * fraction
        if side == OrderSide.BUY:
            return price + slip
        return price - slip

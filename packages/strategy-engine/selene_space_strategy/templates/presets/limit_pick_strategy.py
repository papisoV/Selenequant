"""Limit Pick Strategy — 限价接针 (limit-order dip-taking) on EMA240 trend.

Strategy logic (reference behavior preserved, known bugs fixed):
  1. EMA240 trend judgment. The reference implementation reversed
     klines (``[::-1]``) before computing the EMA because OKX returns
     klines newest-first. Our strategy receives klines oldest-first
     (ascending) from the selene-space market-data layer, so we do NOT
     reverse. This is the fix for [US-A2-03] — applying [::-1] on
     already-ascending data would invert the EMA.
  2. Compute ``average_amplitude`` (mean (high-low)/close * 100 over
     the lookback window) and ``price_atr_ratio`` (price / ATR / 100).
  3. ``selected_value = max(min(amp, price_atr_ratio) * mult, 0.8)``.
  4. Bullish trend (close > EMA240) → place a BUY LIMIT at
     ``price * (1 - selected_value/100)``.
  5. Bearish trend (close < EMA240) → place a SELL LIMIT at
     ``price * (1 + selected_value/100)``.
  6. Convert USDT notional to OKX contract count via
     ``OKXContractConverter`` (legacy sync helper) before emitting.

Output: ``TradeIntent`` with ``order_type=LIMIT`` and ``price`` set.

Integration:
  - Lives in ``templates/presets/`` alongside ``bb_v3``, ``dca_grid``,
    ``ema_triple_cross`` — [US-A2-06] package layering.
  - Returns ``Optional[TradeIntent]`` (not ``Signal``) because the
    downstream pipeline routes limit orders through the TradeIntent
    path (risk_precheck → limit_enforcer → intent → validation).
  - Only active in ``paper`` or ``shadow`` execution modes — live
    trading requires the OKX adapter wiring (out of scope for v015b).

v4 A1 hardening:
  - ``use_tiered_trailing`` defaults to False (TieredTrailingStopTracker
    is A1's deliverable; if A1 is reverted this strategy must still run).
  - ``try/except ImportError`` around TieredTrailingStopTracker import
    so a missing A1 module does not break the strategy engine import.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from selene_space_types.agent import TradeIntent
from selene_space_types.trading import OrderSide, OrderType

# v4 A1: try/except ImportError — if A1 (v015a) is reverted, this
# strategy still loads; trailing-stop integration is simply disabled.
try:  # pragma: no cover - exercised via integration tests when A1 is present
    from selene_space_paper.tiered_trailing_stop import (
        TieredTrailingConfig,
        TieredTrailingStopTracker,
    )

    _TIERED_TRAILING_AVAILABLE = True
except ImportError:  # pragma: no cover
    _TIERED_TRAILING_AVAILABLE = False
    TieredTrailingConfig = None  # type: ignore[assignment,misc]
    TieredTrailingStopTracker = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config (immutable, frozen dataclass per python coding-style rules)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LimitPickConfig:
    """Immutable configuration for the limit-pick (限价接针) strategy.

    All thresholds mirror the reference implementation defaults;
    ``value_multiplier`` and the 0.8 floor included.
    """

    # EMA trend period. Default 240; 0 means "no trend filter,
    # place both long and short".
    ema_period: int = 240
    # ATR / amplitude lookback in bars. Default 60.
    atr_period: int = 60
    # Amplitude lookback in bars (same window as ATR by default).
    amplitude_period: int = 60
    # Multiplier applied to min(amp, price_atr_ratio).
    value_multiplier: float = 2.0
    # Floor for selected_value (%) — forces >= 0.8.
    min_selected_value_pct: float = 0.8
    # USDT notional per order (long). Default 20.
    long_amount_usdt: float = 20.0
    # USDT notional per order (short). Default 20.
    short_amount_usdt: float = 20.0
    # Enable long side (bullish trend).
    enable_long: bool = True
    # Enable short side (bearish trend).
    enable_short: bool = False
    # v4 A1: gate the TieredTrailingStopTracker integration. Defaults
    # to False so the strategy runs even if A1 is reverted mid-flight.
    use_tiered_trailing: bool = False
    # OKX instrument ID (e.g. "BTC-USDT-SWAP") — set when the strategy
    # is wired into a live OKX execution path. For pure paper mode
    # (no contract conversion), leave None and the intent will carry
    # the raw USDT notional as ``quantity``.
    okx_inst_id: Optional[str] = None
    # Minimum bars required before the strategy can emit (ema_period+1).
    min_bars: Optional[int] = None

    def __post_init__(self) -> None:
        if self.ema_period < 0:
            raise ValueError(f"ema_period must be >= 0, got {self.ema_period}")
        if self.atr_period < 1:
            raise ValueError(f"atr_period must be >= 1, got {self.atr_period}")
        if self.amplitude_period < 1:
            raise ValueError(
                f"amplitude_period must be >= 1, got {self.amplitude_period}"
            )
        if self.value_multiplier <= 0:
            raise ValueError(
                f"value_multiplier must be > 0, got {self.value_multiplier}"
            )
        if self.min_selected_value_pct < 0:
            raise ValueError(
                f"min_selected_value_pct must be >= 0, got {self.min_selected_value_pct}"
            )
        if self.long_amount_usdt <= 0:
            raise ValueError(
                f"long_amount_usdt must be > 0, got {self.long_amount_usdt}"
            )
        if self.short_amount_usdt <= 0:
            raise ValueError(
                f"short_amount_usdt must be > 0, got {self.short_amount_usdt}"
            )


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class LimitPickStrategy:
    """Limit-pick (限价接针) strategy — EMA240 trend + ATR-scaled limit price.

    Unlike ``BacktestStrategyTemplate`` subclasses (which emit ``Signal``
    enums consumed by the backtest engine), this strategy emits a
    ``TradeIntent`` directly because the downstream paper/shadow pipeline
    routes limit orders through the intent path (risk_precheck →
    limit_enforcer → intent → validation → execute → audit).

    The strategy is stateless between evaluations (no position tracking
    of its own — position management is delegated to the execution layer
    and, when ``use_tiered_trailing=True``, to the
    ``TieredTrailingStopTracker`` from A1).
    """

    def __init__(
        self,
        config: LimitPickConfig | None = None,
        *,
        contract_converter: Any | None = None,
        public_api: Any | None = None,
    ) -> None:
        """Initialize the limit-pick strategy.

        Args:
            config: Frozen ``LimitPickConfig``. Defaults to a sensible
                reference-style configuration if None.
            contract_converter: Optional ``OKXContractConverter`` for
                async contract sizing (live execution path). When None,
                the strategy emits intents with USDT notional as
                ``quantity`` — fine for paper mode.
            public_api: Optional legacy-style OKX public API object
                exposing ``convert_contract_coin(**params)``. Used by
                the sync ``_convert_contracts`` helper (strategy-engine
                hot path). If both this and ``contract_converter`` are
                None, contract conversion is skipped and intents carry
                the raw USDT notional as ``quantity``.
        """
        self._config = config or LimitPickConfig()
        self._contract_converter = contract_converter
        self._public_api = public_api
        self._min_bars = (
            self._config.min_bars
            if self._config.min_bars is not None
            else max(self._config.ema_period, 2) + 1
        )

        # v4 A1: only construct the tracker if A1 is available AND the
        # user explicitly opted in via ``use_tiered_trailing=True``.
        self._tiered_tracker: Any | None = None
        if self._config.use_tiered_trailing and _TIERED_TRAILING_AVAILABLE:
            self._tiered_tracker = TieredTrailingStopTracker(  # type: ignore[misc]
                TieredTrailingConfig()  # type: ignore[misc]
            )
        elif self._config.use_tiered_trailing and not _TIERED_TRAILING_AVAILABLE:
            logger.warning(
                "limit_pick_strategy: use_tiered_trailing=True but "
                "TieredTrailingStopTracker is unavailable (A1 not installed); "
                "trailing-stop integration disabled."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def config(self) -> LimitPickConfig:
        return self._config

    def evaluate(self, ctx: "LimitPickContext") -> Optional[TradeIntent]:
        """Evaluate market context and emit a limit-order TradeIntent.

        Args:
            ctx: ``LimitPickContext`` carrying the OHLCV DataFrame
                (ascending by timestamp) and the current mark price.

        Returns:
            A ``TradeIntent`` for a BUY or SELL limit order, or None
            if no signal (insufficient bars, neutral trend, or both
            sides disabled).

        The DataFrame is expected to be ascending by timestamp (oldest
        first, newest last). The reference implementation receives
        descending OKX klines and applies [::-1] — we do NOT reverse
        here ([US-A2-03]).
        """
        df = ctx.klines
        if len(df) < self._min_bars:
            return None

        mark_price = ctx.mark_price
        if mark_price <= 0:
            return None

        # === 1. EMA240 trend judgment ===
        is_bullish, is_bearish = self._evaluate_trend(df, mark_price)

        # Both sides disabled — nothing to do.
        if not self._config.enable_long and not self._config.enable_short:
            return None
        # If ema_period == 0, treat both trends as enabled.
        if self._config.ema_period == 0:
            is_bullish = self._config.enable_long
            is_bearish = self._config.enable_short

        # Neither side is active given the trend.
        if not (is_bullish and self._config.enable_long) and not (
            is_bearish and self._config.enable_short
        ):
            return None

        # === 2. average_amplitude + price_atr_ratio ===
        average_amplitude = self._calculate_average_amplitude(df)
        price_atr_ratio = self._calculate_price_atr_ratio(df, mark_price)

        if average_amplitude is None or price_atr_ratio is None:
            return None

        # === 3. selected_value = max(min(amp, ratio) * mult, 0.8) ===
        selected_value = max(
            min(average_amplitude, price_atr_ratio) * self._config.value_multiplier,
            self._config.min_selected_value_pct,
        )

        # === 4. Compute limit price + side + notional ===
        if is_bullish and self._config.enable_long:
            side = OrderSide.BUY
            limit_price = mark_price * (1 - selected_value / 100.0)
            amount_usdt = self._config.long_amount_usdt
            reasoning_op = "limit_pick_long"
        elif is_bearish and self._config.enable_short:
            side = OrderSide.SELL
            limit_price = mark_price * (1 + selected_value / 100.0)
            amount_usdt = self._config.short_amount_usdt
            reasoning_op = "limit_pick_short"
        else:
            return None

        # === 5. Convert USDT notional to contracts (OKX swap) ===
        quantity = self._convert_contracts(
            inst_id=self._config.okx_inst_id,
            amount_usdt=amount_usdt,
            price=limit_price,
            side="buy" if side == OrderSide.BUY else "sell",
        )

        # If contract conversion returned 0 (notional too small for one
        # contract), skip.
        if quantity is not None and quantity <= 0:
            logger.info(
                "limit_pick_strategy: %s computed contract count is 0; skipping.",
                self._config.okx_inst_id,
            )
            return None

        # If no converter wired, fall back to USDT notional as quantity
        # (paper mode without OKX integration).
        if quantity is None:
            quantity = amount_usdt

        reasoning = (
            f"{reasoning_op}: ema={self._config.ema_period} amp={average_amplitude:.3f}% "
            f"atr_ratio={price_atr_ratio:.3f} sel={selected_value:.3f}% "
            f"mark={mark_price:.6f} limit={limit_price:.6f} qty={quantity}"
        )

        return TradeIntent(
            symbol=ctx.symbol,
            side=side,
            quantity=quantity,
            price=limit_price,
            order_type=OrderType.LIMIT,
            reasoning=reasoning,
            confidence=0.7,
            risk_params={
                "selected_value_pct": selected_value,
                "average_amplitude_pct": average_amplitude,
                "price_atr_ratio": price_atr_ratio,
                "ema_period": self._config.ema_period,
                "amount_usdt": amount_usdt,
                "okx_inst_id": self._config.okx_inst_id or "",
                "use_tiered_trailing": self._config.use_tiered_trailing,
            },
            timestamp=int(time.time() * 1000),
        )

    # ------------------------------------------------------------------
    # Indicator helpers — mirror the reference calculations
    # ------------------------------------------------------------------

    def _evaluate_trend(self, df: pd.DataFrame, mark_price: float) -> tuple[bool, bool]:
        """Compute EMA and return (is_bullish, is_bearish).

        [US-A2-03]: ``df`` is ascending (oldest-first, newest-last). We
        compute EMA directly without reversing. The reference
        implementation receives OKX klines descending and applies
        [::-1] — applying [::-1] on our ascending data would invert
        the EMA.
        """
        if self._config.ema_period == 0:
            return True, True

        closes = df["close"]
        if len(closes) < self._config.ema_period:
            return False, False

        # EMA via pandas ewm.
        ema_series = closes.ewm(span=self._config.ema_period, adjust=False).mean()
        ema_value = float(ema_series.iloc[-1])

        if ema_value <= 0:
            return False, False

        # Trend: close above/below the EMA.
        is_bullish = mark_price > ema_value
        is_bearish = mark_price < ema_value
        return is_bullish, is_bearish

    def _calculate_average_amplitude(self, df: pd.DataFrame) -> Optional[float]:
        """Mean of (high-low)/close * 100 over the lookback window.

        ``df`` is ascending; the lookback takes the last ``period``
        rows — same as ``df.tail(period)`` on ascending data.
        """
        period = self._config.amplitude_period
        if len(df) < period:
            # Fall back to all rows if we don't have a full window.
            period = len(df)
        if period <= 0:
            return None

        tail = df.tail(period)
        highs = tail["high"].astype(float)
        lows = tail["low"].astype(float)
        closes = tail["close"].astype(float)

        # Guard against zero close (would divide by zero).
        if (closes <= 0).any():
            return None

        amplitudes = ((highs - lows) / closes) * 100.0
        return float(amplitudes.mean())

    def _calculate_price_atr_ratio(
        self, df: pd.DataFrame, mark_price: float
    ) -> Optional[float]:
        """Compute (mark_price / ATR) / 100.

        ATR is the mean of True Range over the last ``atr_period`` bars.
        True Range = max(high-low, |high-prev_close|, |low-prev_close|).
        """
        period = self._config.atr_period
        if len(df) < period + 1:
            # Need period+1 rows because TR uses prev_close.
            period = len(df) - 1
        if period <= 0:
            return None

        highs = df["high"].astype(float)
        lows = df["low"].astype(float)
        closes = df["close"].astype(float)

        trs: list[float] = []
        for i in range(1, len(df)):
            if len(trs) >= period:
                break
            high = float(highs.iloc[i])
            low = float(lows.iloc[i])
            prev_close = float(closes.iloc[i - 1])
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            trs.append(tr)

        if not trs:
            return None

        atr = sum(trs) / len(trs)
        if atr <= 0:
            return None

        # price_atr_ratio = (mark_price / atr) / 100
        return (mark_price / atr) / 100.0

    # ------------------------------------------------------------------
    # Contract conversion
    # ------------------------------------------------------------------

    def _convert_contracts(
        self,
        *,
        inst_id: Optional[str],
        amount_usdt: float,
        price: float,
        side: str,
    ) -> Optional[float]:
        """Convert USDT notional to OKX contract count.

        Returns:
            Contract count as float, or None if no converter is wired
            (caller falls back to USDT notional as quantity).
        """
        if inst_id is None:
            return None

        # Prefer the sync helper (strategy-engine hot path) — it accepts
        # the legacy-style public_api object.
        if self._public_api is not None:
            return _sync_convert(self._public_api, inst_id, amount_usdt, price, side)

        # Fall back to the async converter if only that was wired. The
        # sync evaluate() path cannot await, so we warn and return None
        # to let the caller fall back to USDT notional.
        if self._contract_converter is not None:
            logger.warning(
                "limit_pick_strategy: async contract_converter wired but "
                "evaluate() is sync; cannot convert. Returning USDT notional."
            )
            return None

        return None


# ---------------------------------------------------------------------------
# Sync conversion helper (isolated for testability)
# ---------------------------------------------------------------------------


def _sync_convert(
    public_api: Any,
    inst_id: str,
    amount_usdt: float,
    price: float,
    side: str,
) -> float:
    """Call the OKX convert-contract-coin API via the legacy public_api.

    Isolated as a module-level function so tests can monkeypatch it
    without constructing a full ``OKXContractConverter`` instance.
    """
    # Late import to avoid a hard dependency from the strategy-engine
    # on the exchange-sdk package (strategy-engine should remain
    # exchange-agnostic in principle). If the exchange-sdk layer is
    # unavailable, fall back to None so the caller uses USDT notional.
    try:
        from selene_space_exchange.exchanges.okx.okx_contract_converter import (
            OKXContractConverter,
        )
    except ImportError:
        logger.warning(
            "limit_pick_strategy: selene_space_exchange not available; "
            "cannot convert contracts. Returning USDT notional."
        )
        return amount_usdt

    return OKXContractConverter.convert_sync(
        public_api,
        inst_id=inst_id,
        amount_usdt=amount_usdt,
        price=price,
        side=side,
    )


# ---------------------------------------------------------------------------
# Context value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LimitPickContext:
    """Immutable market context for the limit-pick strategy.

    Carries everything ``evaluate`` needs without leaking exchange
    client handles or other heavy objects into the strategy.
    """

    symbol: str
    klines: pd.DataFrame
    mark_price: float
    timestamp: int = 0

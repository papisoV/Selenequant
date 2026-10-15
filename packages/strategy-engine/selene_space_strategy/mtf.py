"""Multi-timeframe (MTF) signal fusion framework.

Classic pattern: use a higher timeframe (e.g. 1h) for trend direction
and a lower timeframe (e.g. 15m) for precise entry timing. Only enter
when both timeframes agree (confluence).

Architecture:
  - MTFFrame: holds a strategy runtime for one timeframe
  - MTFConfluence: combines signals from multiple frames with weighted scoring
  - MTFSignal: enriched signal with confluence score and contributing frames
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


class TimeframeRole(str, Enum):
    """Role of a timeframe in the MTF framework."""

    TREND = "trend"       # Higher timeframe: determines direction
    ENTRY = "entry"       # Lower timeframe: determines timing
    CONFIRM = "confirm"   # Additional confirmation timeframe


@dataclass(frozen=True)
class MTFSignal:
    """A signal enriched with multi-timeframe confluence information."""

    signal: Signal
    confluence_score: float  # 0.0 to 1.0
    contributing_frames: dict[str, OrderSide]  # interval → direction
    trend_direction: OrderSide | None = None  # Direction from trend frame
    entry_direction: OrderSide | None = None  # Direction from entry frame


@dataclass
class FrameSignal:
    """Latest signal state from a single timeframe frame."""

    interval: str
    role: TimeframeRole
    side: OrderSide | None = None
    strength: float = 0.0
    timestamp: int = 0


class MTFConfluence:
    """Multi-timeframe confluence engine.

    Combines signals from multiple timeframes using weighted scoring.
    A signal is only emitted when confluence exceeds the threshold.

    Typical setup:
        - 1h (trend, weight 0.6): "Is the trend up or down?"
        - 15m (entry, weight 0.4): "Is this a good entry point?"

    Confluence = weighted sum of agreeing frames / total weight.
    E.g. if 1h says BUY (weight 0.6) and 15m says BUY (weight 0.4),
    confluence = (0.6 + 0.4) / (0.6 + 0.4) = 1.0 (full confluence).

    If 1h says BUY but 15m says SELL, confluence = 0.6 / 1.0 = 0.6
    (partial — trend says buy but entry timing disagrees).

    Args:
        frames: Dict of interval → (role, weight).
        min_confluence: Minimum confluence score to emit a signal (default 0.7).
        require_trend_agreement: If True, signal must agree with trend frame.
    """

    def __init__(
        self,
        frames: dict[str, tuple[TimeframeRole, float]],
        min_confluence: float = 0.7,
        require_trend_agreement: bool = True,
    ) -> None:
        if not frames:
            raise ValueError("Must have at least one timeframe frame")
        if min_confluence < 0 or min_confluence > 1:
            raise ValueError("min_confluence must be in [0, 1]")

        self._frames = frames
        self._min_confluence = min_confluence
        self._require_trend = require_trend_agreement
        self._signals: dict[str, FrameSignal] = {}

        # Validate weights
        total_weight = sum(w for _, w in frames.values())
        if total_weight <= 0:
            raise ValueError("Total weight must be positive")

        # Normalize weights
        self._weights: dict[str, float] = {
            interval: w / total_weight for interval, (_, w) in frames.items()
        }

    def update_signal(self, interval: str, signal: Signal | None) -> None:
        """Update the signal from a specific timeframe.

        Args:
            interval: The timeframe interval (e.g. "1h", "15m").
            signal: The latest signal from this timeframe, or None if no signal.
        """
        if interval not in self._frames:
            return  # Unknown timeframe, ignore

        role, _ = self._frames[interval]

        if signal is None:
            # No new signal — keep existing state
            return

        self._signals[interval] = FrameSignal(
            interval=interval,
            role=role,
            side=signal.side,
            strength=signal.strength,
            timestamp=signal.timestamp,
        )

    def evaluate(self) -> MTFSignal | None:
        """Evaluate confluence across all timeframes.

        Returns:
            MTFSignal if confluence exceeds threshold, None otherwise.
        """
        # Need at least one signal to evaluate
        if not self._signals:
            return None

        # Find the dominant direction (weighted vote)
        buy_weight = 0.0
        sell_weight = 0.0
        contributing: dict[str, OrderSide] = {}

        for interval, frame_sig in self._signals.items():
            weight = self._weights[interval]
            if frame_sig.side == OrderSide.BUY:
                buy_weight += weight
                contributing[interval] = OrderSide.BUY
            elif frame_sig.side == OrderSide.SELL:
                sell_weight += weight
                contributing[interval] = OrderSide.SELL

        # Determine dominant direction
        if buy_weight > sell_weight:
            dominant = OrderSide.BUY
            confluence = buy_weight
        elif sell_weight > buy_weight:
            dominant = OrderSide.SELL
            confluence = sell_weight
        else:
            return None  # Tie — no clear direction

        # Check trend agreement
        trend_direction = self._get_trend_direction()
        if self._require_trend and trend_direction is not None:
            if dominant != trend_direction:
                return None  # Signal disagrees with trend

        # Check confluence threshold
        if confluence < self._min_confluence:
            return None

        # Build the MTF signal
        entry_direction = self._get_entry_direction()
        latest_ts = max(s.timestamp for s in self._signals.values())
        latest_strength = max(s.strength for s in self._signals.values())

        base_signal = Signal(
            strategy_id="mtf_confluence",
            symbol=self._get_common_symbol(),
            side=dominant,
            strength=latest_strength * confluence,
            timestamp=latest_ts,
            reason=f"MTF confluence {confluence:.0%} ({', '.join(f'{k}:{v.value}' for k, v in contributing.items())})",
        )

        return MTFSignal(
            signal=base_signal,
            confluence_score=confluence,
            contributing_frames=contributing,
            trend_direction=trend_direction,
            entry_direction=entry_direction,
        )

    def _get_trend_direction(self) -> OrderSide | None:
        """Get the direction from the trend timeframe."""
        for interval, (role, _) in self._frames.items():
            if role == TimeframeRole.TREND:
                sig = self._signals.get(interval)
                return sig.side if sig else None
        return None

    def _get_entry_direction(self) -> OrderSide | None:
        """Get the direction from the entry timeframe."""
        for interval, (role, _) in self._frames.items():
            if role == TimeframeRole.ENTRY:
                sig = self._signals.get(interval)
                return sig.side if sig else None
        return None

    def _get_common_symbol(self) -> str:
        """Get symbol from any available signal."""
        for sig in self._signals.values():
            if sig.side is not None:
                # We don't store symbol in FrameSignal, use a default
                break
        return "UNKNOWN"

    @property
    def frame_states(self) -> dict[str, "FrameSignal"]:
        """Current signal state for each timeframe."""
        return dict(self._signals)

    @property
    def frames(self) -> dict[str, tuple["TimeframeRole", float]]:
        """Configured timeframe frames: {interval: (role, weight)}."""
        return dict(self._frames)

    def has_interval(self, interval: str) -> bool:
        """Whether this MTF engine is configured for the given interval."""
        return interval in self._frames

    @property
    def main_interval(self) -> str:
        """The trend timeframe's interval (or first frame if no TREND role)."""
        for interval, (role, _w) in self._frames.items():
            if role == TimeframeRole.TREND:
                return interval
        return next(iter(self._frames.keys()))

    def reset(self) -> None:
        """Clear all frame signals."""
        self._signals.clear()


def resample_kline(kline: KlineEvent, target_interval_ms: int) -> KlineEvent | None:
    """Resample a kline event to a higher timeframe.

    This is a placeholder — actual resampling requires accumulating
    bars over the target interval. Use KlineResampler for proper
    OHLCV aggregation.

    Args:
        kline: Source kline bar.
        target_interval_ms: Target interval in milliseconds.

    Returns:
        None — resampling requires state, use KlineResampler instead.
    """
    return None


class KlineResampler:
    """Accumulate lower-timeframe bars into higher-timeframe bars.

    E.g. accumulate 15m bars into 1h bars.

    Args:
        target_interval_ms: Target interval in milliseconds (e.g. 3600000 for 1h).
        source_interval_ms: Source interval in milliseconds (e.g. 900000 for 15m).
    """

    def __init__(
        self,
        target_interval_ms: int,
        source_interval_ms: int = 0,
    ) -> None:
        if target_interval_ms <= 0:
            raise ValueError("target_interval_ms must be positive")
        self._target_ms = target_interval_ms
        self._source_ms = source_interval_ms
        self._buffer: list[KlineEvent] = []
        self._current_bucket: int = 0

    def add_bar(self, kline: KlineEvent) -> KlineEvent | None:
        """Add a source bar and return a resampled bar if the bucket is complete.

        Args:
            kline: Source timeframe kline bar.

        Returns:
            Resampled KlineEvent if bucket is complete, None otherwise.
        """
        # Determine which target bucket this bar belongs to
        bucket = kline.timestamp // self._target_ms

        if self._buffer and bucket != self._current_bucket:
            # New bucket — flush the previous bucket's resampled bar
            self._resample_buffer()
            self._buffer.clear()

        self._current_bucket = bucket
        self._buffer.append(kline)

        # Check if bucket is complete
        if self._source_ms > 0:
            expected_bars = self._target_ms // self._source_ms
            if len(self._buffer) >= expected_bars:
                return self._flush()

        return None

    def flush(self) -> KlineEvent | None:
        """Flush any remaining buffered bars as a partial resampled bar."""
        if not self._buffer:
            return None
        return self._resample_buffer()

    def _flush(self) -> KlineEvent:
        """Emit resampled bar and clear buffer."""
        resampled = self._resample_buffer()
        self._buffer.clear()
        return resampled

    def _resample_buffer(self) -> KlineEvent:
        """Combine buffered bars into a single resampled bar."""
        if not self._buffer:
            raise ValueError("Cannot resample empty buffer")

        first = self._buffer[0]
        open_ = first.open
        close = self._buffer[-1].close
        high = max(b.high for b in self._buffer)
        low = min(b.low for b in self._buffer)
        volume = sum(b.volume for b in self._buffer)
        timestamp = (first.timestamp // self._target_ms) * self._target_ms

        return KlineEvent(
            exchange=first.exchange,
            symbol=first.symbol,
            timestamp=timestamp,
            interval=f"{self._target_ms // 60000}m" if self._target_ms < 86400000 else "1d",
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            closed=True,
        )

    def reset(self) -> None:
        """Clear buffer and reset state."""
        self._buffer.clear()
        self._current_bucket = 0

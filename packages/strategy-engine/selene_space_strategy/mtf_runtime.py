"""MTFRuntimeAggregator — multi-timeframe signal aggregation around StrategyRuntime.

Architecture (per architect feedback v2.0.0):
    - StrategyRuntime itself stays single-interval (backward compatible).
    - MTFRuntimeAggregator wraps a main runtime + MTFConfluence + optional
      secondary runtimes (one per non-main interval).
    - On main-interval kline: main runtime produces Signal -> fed to MTFConfluence
      -> mtf.evaluate() decides whether to emit (confluence threshold + trend
      agreement).
    - On secondary-interval kline: secondary runtime produces Signal -> fed to
      MTFConfluence as state update only (no emit).
    - On unknown-interval kline: ignored.

This keeps MTF logic out of StrategyRuntime (which still serves single-interval
paths byte-identically) and lets the upper layer (backtest engine / test) wire
multiple runtimes together.
"""

from __future__ import annotations

from selene_space_strategy.mtf import MTFConfluence
from selene_space_strategy.runtime import StrategyRuntime
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class MTFRuntimeAggregator:
    """Aggregate signals from multiple timeframes via MTFConfluence.

    Args:
        main_runtime: StrategyRuntime for the main interval (the one that
            emits trading signals after confluence).
        mtf: MTFConfluence engine configured with frames dict like
            {main_interval: (TimeframeRole.TREND, w1), "15m": (TimeframeRole.ENTRY, w2)}.
        secondary_runtimes: Optional mapping of interval -> StrategyRuntime for
            non-main timeframes. When a kline arrives for one of these intervals,
            the corresponding runtime produces a Signal that is fed to mtf (as
            state update) but does not itself emit.
    """

    def __init__(
        self,
        main_runtime: StrategyRuntime,
        mtf: MTFConfluence,
        secondary_runtimes: dict[str, StrategyRuntime] | None = None,
    ) -> None:
        self._main_runtime = main_runtime
        self._mtf = mtf
        self._secondary_runtimes = secondary_runtimes or {}
        self._main_interval = mtf.main_interval

    @property
    def main_interval(self) -> str:
        """The interval whose klines drive signal emission."""
        return self._main_interval

    def on_kline(self, event: KlineEvent) -> Signal | None:
        """Process a kline event and optionally emit a confluence-verified Signal.

        Args:
            event: KlineEvent from the market data stream. May belong to the
                main interval or any secondary interval registered in mtf frames.

        Returns:
            A Signal if the event belongs to the main interval AND mtf.evaluate()
            returns a confluence signal; None otherwise. Secondary-interval
            events never emit directly — they only update mtf state.
        """
        interval = event.interval

        # Unknown interval — ignore
        if not self._mtf.has_interval(interval):
            return None

        # Secondary interval: produce signal, feed to mtf, no emit
        if interval != self._main_interval:
            secondary_runtime = self._secondary_runtimes.get(interval)
            if secondary_runtime is None:
                self._mtf.update_signal(interval, None)
                return None
            secondary_signal = secondary_runtime.on_kline(event)
            self._mtf.update_signal(interval, secondary_signal)
            return None

        # Main interval: produce signal, feed to mtf, then evaluate
        main_signal = self._main_runtime.on_kline(event)
        self._mtf.update_signal(interval, main_signal)

        mtf_signal = self._mtf.evaluate()
        if mtf_signal is None:
            return None
        return mtf_signal.signal

    def reset(self) -> None:
        """Reset all runtimes and the MTF confluence state."""
        self._main_runtime.reset()
        for runtime in self._secondary_runtimes.values():
            runtime.reset()
        self._mtf.reset()


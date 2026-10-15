"""Stochastic Oscillator — Lane's range-position gauge."""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator


class StochasticResult:
    """The %K / %D pairing.

    Attributes:
        k: once-smoothed range position of the close.
        d: signal line — a further mean taken across ``k``.
    """

    __slots__ = ("d", "k")

    def __init__(self, k: pd.Series, d: pd.Series) -> None:
        self.k = k
        self.d = d


class Stochastic(Indicator):
    """Where the close sits inside its recent high-low envelope.

    George Lane's question was never "which way is price going" but
    "where inside its own territory is it right now". The recipe:

    1. Track a ``period``-bar rolling maximum of highs and minimum of
       lows — the envelope.
    2. Express the close as a fraction of that corridor,
       ``(close - floor) / corridor``, scaled onto 0-100. Raw stochastic.
    3. Average the fraction over ``smooth_k`` bars -> %K.
    4. Average %K over ``d_period`` bars -> %D, the trigger line.

    Trading notes worth keeping beside the code: readings above 80 flag
    stretched conditions, below 20 washed-out ones; %K crossing %D reads
    as a fade cue in ranges and a continuation cue in trends. Same
    construction as the SMA-smoothed variant of TradingView's ``ta.stoch``.

    Edge handling:

    * Partial windows during warm-up are permitted (``min_periods=1``)
      so the envelope exists from bar zero; finished lines get masked
      afterwards rather than computed short.
    * A bar whose window has zero width (high equals low across the
      lookback — typical of illiquid stretches) leaves the fraction
      undefined: NaN, never an infinity.
    * Masking: %K stays NaN through bar ``period + smooth_k - 1``; %D
      needs another ``d_period - 1`` completed %K bars beyond that.
    """

    _DEFAULT_SMOOTH_K = 3
    _DEFAULT_D = 3

    def __init__(
        self,
        period: int = 14,
        smooth_k: int = _DEFAULT_SMOOTH_K,
        d_period: int = _DEFAULT_D,
    ) -> None:
        super().__init__(period)
        for label, value in (("smooth_k", smooth_k), ("d_period", d_period)):
            if value <= 0:
                raise ValueError(f"{label} must be positive, got {value}")
        self._smooth_k = smooth_k
        self._d_period = d_period

    @property
    def name(self) -> str:
        return "stoch"

    @property
    def smooth_k(self) -> int:
        """Bars averaged into the raw position to yield %K."""
        return self._smooth_k

    @property
    def d_period(self) -> int:
        """Bars averaged across %K to yield %D."""
        return self._d_period

    def _range_bounds(
        self, highs: np.ndarray, lows: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Envelope extremes: trailing max over highs, min over lows."""
        ceiling = pd.Series(highs).rolling(self._period, min_periods=1).max().to_numpy()
        floor = pd.Series(lows).rolling(self._period, min_periods=1).min().to_numpy()
        return ceiling, floor

    def _raw_placement(
        self, closes: np.ndarray, ceiling: np.ndarray, floor: np.ndarray
    ) -> np.ndarray:
        """Close's 0-100 position within each bar's envelope.

        Rows whose corridor has collapsed to zero width get NaN — the
        division guard lives here, before any smoothing sees the data.
        """
        n_rows = len(closes)
        corridor = ceiling - floor
        has_room = corridor > 0.0
        placement = np.full(n_rows, np.nan)
        placement[has_room] = (closes[has_room] - floor[has_room]) / corridor[has_room]
        return 100.0 * placement

    @staticmethod
    def _running_mean(series: pd.Series, width: int) -> pd.Series:
        """Trailing mean that tolerates partial windows at the head."""
        return series.rolling(width, min_periods=1).mean()

    def _blank_warmup(self, fast: pd.Series, slow: pd.Series) -> None:
        """Hide rows ahead of fully-smoothed territory, in place.

        %K needs ``period`` envelope bars plus ``smooth_k - 1`` extra to
        settle; %D stacks ``d_period - 1`` more on top of that.
        """
        settled = self._period + self._smooth_k - 1
        fast.iloc[:settled] = np.nan
        slow.iloc[: settled + self._d_period - 1] = np.nan

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Produce the fast %K line on its own.

        Args:
            data: Frame carrying ``"high"``, ``"low"``, ``"close"``.

        Returns:
            Series in [0, 100]; see :meth:`calculate_full` for masking
            details and for obtaining %D alongside it.
        """
        return self.calculate_full(data).k

    def calculate_full(self, data: pd.DataFrame) -> StochasticResult:
        """Derive both lines in one pass.

        Args:
            data: OHLC frame; needs the three columns listed under
                :meth:`calculate`.

        Returns:
            A :class:`StochasticResult` holding the masked pair.
        """
        ceiling, floor = self._range_bounds(
            data["high"].to_numpy(dtype="float64"),
            data["low"].to_numpy(dtype="float64"),
        )
        scaled = pd.Series(
            self._raw_placement(
                data["close"].to_numpy(dtype="float64"), ceiling, floor
            ),
            index=data.index,
        )

        fast_line = self._running_mean(scaled, self._smooth_k)
        slow_line = self._running_mean(fast_line, self._d_period)
        self._blank_warmup(fast_line, slow_line)

        return StochasticResult(k=fast_line, d=slow_line)

    def __repr__(self) -> str:
        return f"Stochastic(k={self._period}, smooth_k={self._smooth_k}, d={self._d_period})"

"""Average Directional Index with +DI / -DI (Wilder's DMI system)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator


def _wilder_weight(span: int) -> float:
    """Per-bar decay for a Wilder span, matching pandas exactly.

    pandas re-derives any ``alpha`` through its center-of-mass form —
    ``com = (1-alpha)/alpha`` then ``weight = 1/(1+com)``. The detour nudges
    the weight by an ulp for certain spans, so repeating it here keeps the
    hand-written recursion bit-equal to ``ewm(alpha=1/span, adjust=False)``.
    """
    alpha = 1.0 / span
    return 1.0 / (1.0 + (1.0 - alpha) / alpha)


class ADXResult:
    """Bundle of the DMI outputs.

    Attributes:
        adx: trend-strength reading in [0, 100].
        plus_di: upward directional indicator (+DI).
        minus_di: downward directional indicator (-DI).
    """

    __slots__ = ("adx", "minus_di", "plus_di")

    def __init__(self, adx: pd.Series, plus_di: pd.Series, minus_di: pd.Series) -> None:
        self.adx = adx
        self.plus_di = plus_di
        self.minus_di = minus_di


class ADX(Indicator):
    """Average Directional Index — Wilder's Directional Movement system.

    Trend strength only, direction-agnostic. The chain of computations:

    1. True range per bar (same definition ATR uses).
    2. Directional movement: +DM counts bars whose upside progress beats
       their downside progress; -DM the reverse. Weaker move of the pair is
       discarded, and non-positive moves contribute zero.
    3. TR, +DM and -DM each pass through Wilder's recursion (weight
       ``1/period``).
    4. +DI = 100 * smoothed(+DM) / smoothed(TR); same for -DI.
    5. DX compresses the DI spread: ``100 * |+DI - -DI| / (+DI + -DI)``.
    6. ADX = Wilder smoothing of DX over ``adx_period``.

    Mirrors TradingView's ``ta.dmi(di_length, adx_smoothing)``. Reading
    guide: below 25 weak or absent trend, 25-50 strong, 50-75 very strong,
    above 75 extreme.
    """

    def __init__(
        self,
        period: int = 14,
        adx_period: int | None = None,
    ) -> None:
        super().__init__(period)
        if adx_period is not None and adx_period <= 0:
            raise ValueError(f"adx_period must be positive, got {adx_period}")
        self._adx_period = adx_period if adx_period is not None else period

    @property
    def name(self) -> str:
        return "adx"

    @property
    def adx_period(self) -> int:
        """Smoothing window applied to DX to obtain ADX."""
        return self._adx_period

    def _wilder(self, values: np.ndarray, span: int) -> np.ndarray:
        """Wilder recursion, bit-equal to ``ewm(alpha=1/span, adjust=False)``.

        The full pandas rule set: a NaN row still decays the carried weight
        once (holes are not free under ``ignore_na=False``), the following
        observation blends through the accumulated weight before the ledger
        resets, and consecutive equal readings skip the update entirely.
        """
        if values.size == 0:
            return values.copy()
        weight = _wilder_weight(span)
        step_down = 1.0 - weight
        out = np.empty_like(values)
        level = values[0]
        started = level == level
        out[0] = level
        backlog = 1.0
        for pos in range(1, len(values)):
            reading = values[pos]
            counted = reading == reading
            if started:
                backlog *= step_down
                if counted:
                    if level != reading:
                        level = backlog * level + weight * reading
                        level /= backlog + weight
                    backlog = 1.0
                out[pos] = level
            elif counted:
                level = reading
                backlog = 1.0
                started = True
                out[pos] = level
            else:
                out[pos] = np.nan
        return out

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Return the ADX line by itself.

        Args:
            data: Frame exposing ``"high"``, ``"low"`` and ``"close"``.

        Returns:
            Series of ADX readings in [0, 100]. See :meth:`calculate_full`
            for the complete DMI set.
        """
        return self.calculate_full(data).adx

    def calculate_full(self, data: pd.DataFrame) -> ADXResult:
        """Compute ADX together with both directional lines.

        Args:
            data: Frame exposing ``"high"``, ``"low"`` and ``"close"``.

        Returns:
            An :class:`ADXResult`; rows before ``2 * max(period,
            adx_period)`` are NaN on every series.
        """
        highs = data["high"].to_numpy(dtype="float64")
        lows = data["low"].to_numpy(dtype="float64")
        closes = data["close"].to_numpy(dtype="float64")

        # --- true range (row max skips missing components, pandas-style) ---
        shifted_close = np.concatenate(([closes[0]], closes[:-1]))
        candidates = np.vstack(
            (highs - lows, np.abs(highs - shifted_close), np.abs(lows - shifted_close))
        )
        with np.errstate(invalid="ignore"):
            true_range = np.nanmax(candidates, axis=0)

        # --- directional movement ---
        prev_high = np.concatenate(([highs[0]], highs[:-1]))
        prev_low = np.concatenate(([lows[0]], lows[:-1]))
        rise = highs - prev_high
        fall = prev_low - lows

        plus_move = np.where((rise > fall) & (rise > 0.0), rise, 0.0)
        minus_move = np.where((fall > rise) & (fall > 0.0), fall, 0.0)
        # --- Wilder smoothing across all three streams ---
        smooth_tr = self._wilder(true_range, self._period)
        smooth_plus = self._wilder(plus_move, self._period)
        smooth_minus = self._wilder(minus_move, self._period)

        di_up = 100.0 * (smooth_plus / smooth_tr)
        di_down = 100.0 * (smooth_minus / smooth_tr)

        combined = di_up + di_down
        with np.errstate(divide="ignore", invalid="ignore"):
            dx = 100.0 * (np.abs(di_up - di_down) / combined)

        adx_values = self._wilder(dx, self._adx_period)

        hide = max(self._period, self._adx_period) * 2
        idx = data.index
        result = ADXResult(
            adx=pd.Series(adx_values, index=idx),
            plus_di=pd.Series(di_up, index=idx),
            minus_di=pd.Series(di_down, index=idx),
        )
        result.adx.iloc[:hide] = np.nan
        result.plus_di.iloc[:hide] = np.nan
        result.minus_di.iloc[:hide] = np.nan
        return result

    def __repr__(self) -> str:
        if self._adx_period == self._period:
            return f"ADX(period={self._period})"
        return f"ADX(di_period={self._period}, adx_period={self._adx_period})"

"""Chainable indicator access layer for strategy authoring.

Strategies inherit :class:`IndicatorMixIn` to gain shorthand builders
(``sma``, ``rsi``, ``macd``, ...) that delegate to the shared indicator
catalog, plus a tiny predicate vocabulary (``above``/``below``/
``crosses_above``/...) evaluated on the most recent bars of the produced
series. Computed values travel in lightweight wrapper objects so call
sites read like sentences rather than dataframe plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class IndicatorWrapper:
    """A finished indicator series bundled with provenance.

    Attributes:
        name: catalog key the series was produced under.
        series: aligned output values (may open with NaN warm-up).
        params: keyword arguments the indicator was built with.
    """

    name: str
    series: pd.Series
    params: dict[str, Any]

    def value_at(self, index: int = -1) -> float | None:
        """Numeric reading at ``index`` (negative counts from the end).

        Out-of-range positions and NaN cells both collapse to ``None``
        so callers can branch on a single sentinel.
        """
        if abs(index) >= len(self.series):
            return None
        cell = self.series.iloc[index]
        return cell if pd.notna(cell) else None

    def last(self) -> float | None:
        """Most recent usable reading."""
        return self.value_at(-1)

    def previous(self) -> float | None:
        """Reading one bar before the latest."""
        return self.value_at(-2)

    def _side_by_side(
        self, counterpart: "IndicatorWrapper | float"
    ) -> tuple[float, float] | None:
        """Pair our latest reading against a sibling series or constant."""
        ours = self.last()
        if ours is None:
            return None
        if isinstance(counterpart, IndicatorWrapper):
            theirs = counterpart.last()
            if theirs is None:
                return None
        else:
            theirs = counterpart
        return ours, theirs

    def above(self, other: "IndicatorWrapper | float") -> bool:
        """True when the latest value sits strictly over ``other``."""
        pair = self._side_by_side(other)
        return bool(pair is not None and pair[0] > pair[1])

    def below(self, other: "IndicatorWrapper | float") -> bool:
        """True when the latest value sits strictly under ``other``."""
        pair = self._side_by_side(other)
        return bool(pair is not None and pair[0] < pair[1])

    @staticmethod
    def _both_readable(*candidates: float | None) -> bool:
        return all(c is not None for c in candidates)

    def crosses_above(self, other: IndicatorWrapper) -> bool:
        """True when we moved from at-or-below to strictly above on the newest bar."""
        a_prev, a_now = self.previous(), self.last()
        b_prev, b_now = other.previous(), other.last()
        if not self._both_readable(a_prev, a_now, b_prev, b_now):
            return False
        return bool(a_prev <= b_prev and a_now > b_now)

    def crosses_below(self, other: IndicatorWrapper) -> bool:
        """True when we moved from at-or-above to strictly below on the newest bar."""
        a_prev, a_now = self.previous(), self.last()
        b_prev, b_now = other.previous(), other.last()
        if not self._both_readable(a_prev, a_now, b_prev, b_now):
            return False
        return bool(a_prev >= b_prev and a_now < b_now)


@dataclass
class MACDWrapper:
    """All three MACD streams kept side by side.

    Attributes:
        name: fixed catalog tag for the composite.
        macd_line: fast-minus-slow EMA spread.
        signal_line: smoothed version of the spread.
        histogram: spread minus its smoothing.
        params: periods the composite was built from.
    """

    name: str = "macd"
    macd_line: pd.Series | None = None
    signal_line: pd.Series | None = None
    histogram: pd.Series | None = None
    params: dict[str, Any] | None = None

    def _stream(self, tag: str, values: pd.Series | None) -> IndicatorWrapper:
        return IndicatorWrapper(tag, values, self.params or {})

    def macd(self) -> IndicatorWrapper:
        """Spread stream as a standard wrapper."""
        return self._stream("macd_line", self.macd_line)

    def signal(self) -> IndicatorWrapper:
        """Smoothing stream as a standard wrapper."""
        return self._stream("signal_line", self.signal_line)

    def hist(self) -> IndicatorWrapper:
        """Histogram stream as a standard wrapper."""
        return self._stream("histogram", self.histogram)

    def _terminal_two(self) -> tuple[float | None, float | None]:
        stream = self.hist()
        return stream.previous(), stream.last()

    def histogram_positive(self) -> bool:
        """Latest histogram bar above zero."""
        current = self.hist().last()
        return bool(current is not None and current > 0)

    def histogram_negative(self) -> bool:
        """Latest histogram bar below zero."""
        current = self.hist().last()
        return bool(current is not None and current < 0)

    def histogram_crosses_up(self) -> bool:
        """Histogram flipped from non-positive to positive."""
        prior, current = self._terminal_two()
        return bool(prior is not None and current is not None and prior <= 0 < current)

    def histogram_crosses_down(self) -> bool:
        """Histogram flipped from non-negative to negative."""
        prior, current = self._terminal_two()
        return bool(prior is not None and current is not None and prior >= 0 > current)


class IndicatorMixIn:
    """Shorthand indicator builders for strategy classes.

    Mix this into a template and call e.g. ``self.rsi(data, period=14)``;
    results arrive pre-wrapped so threshold checks and cross predicates
    work immediately. Unknown catalog names route through :meth:`compute`.
    """

    def _get_registry(self):
        """Resolve the process-wide indicator catalog on demand."""
        from selene_space_strategy.indicators.registry import indicator_registry
        return indicator_registry

    # -- generic entry point -------------------------------------------------

    def compute(self, name: str, data: pd.DataFrame, **params):
        """Build and evaluate any catalogued indicator.

        Multi-stream indicators get dedicated handling (``macd`` yields a
        :class:`MACDWrapper`, ``bbands`` surfaces the middle band);
        everything else accepts arbitrary keyword parameters and falls
        back to a plain ``period`` when the class rejects extras.
        """
        handler = {
            "macd": self._evaluate_macd,
            "bbands": self._evaluate_bbands,
        }.get(name)
        if handler is not None:
            return handler(data, params)

        indicator_cls = self._get_registry().get(name)
        try:
            instance = indicator_cls(**params)
        except TypeError:
            instance = indicator_cls(period=params.get("period", 14))
        return IndicatorWrapper(name, instance.calculate(data), params)

    # -- multi-stream builders -------------------------------------------------

    def _resolve_macd_periods(self, options: dict[str, Any]) -> tuple[int, int, int]:
        fast = options.get("fast_period", 12)
        slow = options.get("slow_period", options.get("period", 26))
        trigger = options.get("signal_period", 9)
        return fast, slow, trigger

    def _evaluate_macd(self, data: pd.DataFrame, options: dict[str, Any]) -> MACDWrapper:
        fast, slow, trigger = self._resolve_macd_periods(options)
        composite = self._get_registry().get("macd")(
            period=slow, fast_period=fast, signal_period=trigger
        )
        parts = composite.calculate_full(data)
        return MACDWrapper(
            macd_line=parts.macd_line,
            signal_line=parts.signal_line,
            histogram=parts.histogram,
            params={"fast": fast, "slow": slow, "signal": trigger},
        )

    def _evaluate_bbands(self, data: pd.DataFrame, options: dict[str, Any]) -> IndicatorWrapper:
        width = options.get("period", 20)
        spread = options.get("std", options.get("deviation", 2.0))
        bands_cls = self._get_registry().get("bbands")
        bands = bands_cls(period=width, num_std=spread)
        parts = bands.calculate_full(data)
        return IndicatorWrapper(
            "bbands", parts.middle, {"period": width, "std": spread}
        )

    # -- moving-average family -------------------------------------------------

    def ma(self, data: pd.DataFrame, period: int = 20) -> IndicatorWrapper:
        """Alias of :meth:`sma`."""
        return self.sma(data, period)

    def sma(self, data: pd.DataFrame, period: int = 20) -> IndicatorWrapper:
        """Simple moving average of closes."""
        return self.compute("sma", data, period=period)

    def ema(self, data: pd.DataFrame, period: int = 20) -> IndicatorWrapper:
        """Exponential moving average of closes."""
        return self.compute("ema", data, period=period)

    def boll(self, data: pd.DataFrame, period: int = 20, std: float = 2.0) -> IndicatorWrapper:
        """Alias of :meth:`bbands` (middle band)."""
        return self.bbands(data, period, std)

    def bbands(self, data: pd.DataFrame, period: int = 20, std: float = 2.0) -> IndicatorWrapper:
        """Bollinger setup; returns the middle band series."""
        return self.compute("bbands", data, period=period, std=std)

    # -- momentum family -------------------------------------------------

    def rsi(self, data: pd.DataFrame, period: int = 14) -> IndicatorWrapper:
        """Relative strength index scaled 0-100."""
        return self.compute("rsi", data, period=period)

    def macd(
        self,
        data: pd.DataFrame,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> MACDWrapper:
        """Full MACD composite (spread, trigger, histogram)."""
        return self.compute(
            "macd", data, fast_period=fast, slow_period=slow, signal_period=signal
        )

    def stoch(self, data: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> IndicatorWrapper:
        """Stochastic oscillator; only the %K length is forwarded."""
        return self.compute("stoch", data, period=k_period)

    def kdj(
        self,
        data: pd.DataFrame,
        rsv_period: int = 9,
        k_period: int = 3,
        d_period: int = 3,
    ) -> IndicatorWrapper:
        """KDJ oscillator; only the RSV length is forwarded."""
        return self.compute("kdj", data, period=rsv_period)

    # -- volatility / trend strength -------------------------------------------------

    def atr(self, data: pd.DataFrame, period: int = 14) -> IndicatorWrapper:
        """Average true range."""
        return self.compute("atr", data, period=period)

    def adx(self, data: pd.DataFrame, period: int = 14) -> IndicatorWrapper:
        """Average directional index."""
        return self.compute("adx", data, period=period)

    # -- crossover predicates -------------------------------------------------

    def cross_above(self, fast: IndicatorWrapper, slow: IndicatorWrapper) -> bool:
        """Did ``fast`` cross over ``slow`` on the newest bar."""
        return fast.crosses_above(slow)

    def cross_below(self, fast: IndicatorWrapper, slow: IndicatorWrapper) -> bool:
        """Did ``fast`` cross under ``slow`` on the newest bar."""
        return fast.crosses_below(slow)

    def golden_cross(self, data: pd.DataFrame, fast: int = 10, slow: int = 50) -> bool:
        """Short SMA crossed over long SMA on the newest bar."""
        return self.sma(data, fast).crosses_above(self.sma(data, slow))

    def death_cross(self, data: pd.DataFrame, fast: int = 10, slow: int = 50) -> bool:
        """Short SMA crossed under long SMA on the newest bar."""
        return self.sma(data, fast).crosses_below(self.sma(data, slow))

    # -- statistical toolkit -------------------------------------------------

    def hurst(self, data: pd.DataFrame, max_lag: int = 20) -> IndicatorWrapper:
        """Rescaled-range Hurst exponent (<0.5 reverts, >0.5 trends)."""
        return self.compute("hurst", data, max_lag=max_lag)

    def zscore(self, data: pd.DataFrame, window: int = 20) -> IndicatorWrapper:
        """Rolling standardized distance from the mean."""
        return self.compute("zscore", data, window=window)

    def half_life(self, data: pd.DataFrame) -> IndicatorWrapper:
        """OU-process decay speed of the logged series."""
        return self.compute("half_life", data)

    # -- volume family -------------------------------------------------

    def obv(self, data: pd.DataFrame) -> IndicatorWrapper:
        """On-balance volume, signed cumulative flow."""
        return self.compute("obv", data)

    def cmf(self, data: pd.DataFrame, period: int = 20) -> IndicatorWrapper:
        """Chaikin money flow over ``period`` bars."""
        return self.compute("cmf", data, period=period)

    def mfi(self, data: pd.DataFrame, period: int = 14) -> IndicatorWrapper:
        """Money flow index, a volume-weighted RSI analogue."""
        return self.compute("mfi", data, period=period)

    def force_index(self, data: pd.DataFrame, period: int = 13) -> IndicatorWrapper:
        """Smoothed price-change-times-volume thrust."""
        return self.compute("force_index", data, period=period)

    # -- directional regime gate -------------------------------------------------

    def check_trend_filter(
        self,
        data: pd.DataFrame,
        adx_length: int = 14,
        adx_threshold: float = 25.0,
        direction: str = "long",
    ) -> tuple[bool, float]:
        """Gate entries on ADX strength with an optional DI-side constraint.

        Returns ``(passing, adx_value)``. Warm-up bars (any NaN among the
        terminal ADX/DI readings) fail closed as ``(False, 0.0)``. With
        ``direction="long"`` a minus-DI dominance vetoes; ``"short"``
        vetoes on plus-DI dominance; ``"any"`` skips the veto.
        """
        from selene_space_strategy.indicators.adx import ADX

        engine = ADX(adx_length)
        breakdown = engine.calculate_full(data)
        tip = len(data) - 1
        strength = breakdown.adx.iloc[tip]
        buyers = breakdown.plus_di.iloc[tip]
        sellers = breakdown.minus_di.iloc[tip]

        if pd.isna(strength) or pd.isna(buyers) or pd.isna(sellers):
            return False, 0.0

        if direction == "long" and buyers < sellers:
            return False, float(strength)
        if direction == "short" and sellers < buyers:
            return False, float(strength)

        return bool(strength > adx_threshold), float(strength)

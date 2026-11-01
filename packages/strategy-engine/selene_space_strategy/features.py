"""Shared feature container for multi-strategy computation.

Computing the same indicator once per strategy wastes work when several
strategies trade the same symbol. :class:`FeatureVector` is the frozen
carrier for "price frame + precomputed indicator columns" so downstream
consumers can share one calculation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

# Optional slots in declaration order; drives to_dict() flattening.
_OPTIONAL_SLOTS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "volume",
    "rsi_14",
    "macd",
    "macd_signal",
    "bb_upper",
    "bb_lower",
    "atr_14",
)

_SLOT_NAMES = frozenset({"close", *_OPTIONAL_SLOTS})

# OHLCV columns lifted verbatim off the source frame.
_PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "volume")


def _meaningful_index(df: pd.DataFrame) -> pd.Index | None:
    """Keep a meaningful index; plain RangeIndex carries no information."""
    if isinstance(df.index, pd.RangeIndex):
        return None
    return df.index


def _price_kwargs(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Collect whichever OHLCV columns exist on ``df``."""
    return {col: df[col] for col in _PRICE_COLUMNS if col in df.columns}


@dataclass(frozen=True)
class FeatureVector:
    """Immutable bundle of one OHLCV frame plus optional derived series.

    Named slots cover the common indicators (RSI/MACD/Bollinger/ATR at their
    conventional periods); anything else goes into ``extra``. ``get`` /
    ``has`` treat both uniformly.
    """

    close: pd.Series
    timestamp: pd.Index | None = None
    open: pd.Series | None = None
    high: pd.Series | None = None
    low: pd.Series | None = None
    volume: pd.Series | None = None

    rsi_14: pd.Series | None = None
    macd: pd.Series | None = None
    macd_signal: pd.Series | None = None
    bb_upper: pd.Series | None = None
    bb_lower: pd.Series | None = None
    atr_14: pd.Series | None = None

    # Anything beyond the named slots
    extra: dict[str, pd.Series] = field(default_factory=dict)

    def get(self, name: str) -> pd.Series | None:
        """Look up an indicator by slot or extra key; None when absent."""
        if name in _SLOT_NAMES:
            return getattr(self, name)
        return self.extra.get(name)

    def has(self, name: str) -> bool:
        """True when ``name`` resolves to a non-None series."""
        return self.get(name) is not None

    def to_dict(self) -> dict[str, Any]:
        """Flatten populated slots (plus ``extra``) into a plain mapping."""
        payload: dict[str, Any] = {"close": self.close}
        if self.timestamp is not None:
            payload["timestamp"] = self.timestamp
        payload.update(
            {slot: value for slot in _OPTIONAL_SLOTS
             if (value := getattr(self, slot)) is not None}
        )
        if self.extra:
            payload["extra"] = self.extra
        return payload

    @classmethod
    def _assemble(
        cls,
        df: pd.DataFrame,
        indicators: dict[str, Any],
    ) -> FeatureVector:
        """Single construction funnel: price lift + index + indicators."""
        fields: dict[str, Any] = {"close": df["close"]}
        fields["timestamp"] = _meaningful_index(df)
        fields.update(_price_kwargs(df))
        fields.update(indicators)
        return cls(**fields)

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> FeatureVector:
        """Wrap raw OHLCV columns with no indicators computed."""
        return cls._assemble(df, {})

    @classmethod
    def from_indicators(
        cls,
        df: pd.DataFrame,
        *,
        rsi_14: pd.Series | None = None,
        macd: pd.Series | None = None,
        macd_signal: pd.Series | None = None,
        bb_upper: pd.Series | None = None,
        bb_lower: pd.Series | None = None,
        atr_14: pd.Series | None = None,
        extra: dict[str, pd.Series] | None = None,
    ) -> FeatureVector:
        """Wrap a frame together with caller-supplied indicator series."""
        supplied: dict[str, Any] = {
            name: value
            for name, value in (
                ("rsi_14", rsi_14),
                ("macd", macd),
                ("macd_signal", macd_signal),
                ("bb_upper", bb_upper),
                ("bb_lower", bb_lower),
                ("atr_14", atr_14),
            )
            if value is not None
        }
        supplied["extra"] = extra or {}
        return cls._assemble(df, supplied)

    @classmethod
    def compute_standard(
        cls,
        df: pd.DataFrame,
        *,
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        bb_period: int = 20,
        bb_std: float = 2.0,
        atr_period: int = 14,
    ) -> FeatureVector:
        """Compute the full standard indicator set and wrap everything.

        ATR is skipped silently when the frame lacks high/low columns.
        """
        from selene_space_strategy.indicators.atr import ATR
        from selene_space_strategy.indicators.bbands import BollingerBands
        from selene_space_strategy.indicators.macd import MACD
        from selene_space_strategy.indicators.rsi import RSI

        macd_bundle = MACD(
            period=macd_slow, fast_period=macd_fast, signal_period=macd_signal
        ).calculate_full(df)
        bb_bundle = BollingerBands(period=bb_period, num_std=bb_std).calculate_full(df)
        has_hl = {"high", "low"} <= set(df.columns)

        return cls._assemble(
            df,
            {
                "rsi_14": RSI(rsi_period).calculate(df),
                "macd": macd_bundle.macd_line,
                "macd_signal": macd_bundle.signal_line,
                "bb_upper": bb_bundle.upper,
                "bb_lower": bb_bundle.lower,
                "atr_14": ATR(atr_period).calculate(df) if has_hl else None,
            },
        )

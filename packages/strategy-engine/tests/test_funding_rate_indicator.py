"""Funding-rate indicators: a missing column must not become a fabricated 0.0.

The derivatives columns (``FUNDING_RATE`` / ``FUNDING_RATE_Z``) only exist
when the loaders that populate them have run. When they have not, the
indicator used to hand back a constant 0.0 — a value that is *semantically
valid* (a neutral funding rate), so nothing downstream could tell "flat
market" apart from "no data at all".

The runtime already knows how to wait on absent data: it skips a bar whose
indicator column reads NaN. Reporting NaN here hands the bar to that existing
gate instead of inventing a number, and leaves a log line explaining why.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from selene_space_strategy.indicators.funding_rate import (
    FundingRateIndicator,
    FundingRateZScore,
)

_INDICATORS = [
    (FundingRateIndicator, "FUNDING_RATE"),
    (FundingRateZScore, "FUNDING_RATE_Z"),
]


def _ohlcv_only(n: int = 12) -> pd.DataFrame:
    """A frame with the columns a plain kline stream carries — and no funding."""
    closes = 100.0 + np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + 1.0,
            "low": closes - 1.0,
            "close": closes,
            "volume": np.full(n, 10.0),
        }
    )


class TestMissingColumnIsNotFabricated:
    @pytest.mark.parametrize("cls,column", _INDICATORS)
    def test_missing_column_yields_all_nan(self, cls: type, column: str) -> None:
        """No funding column → NaN everywhere, never a finite stand-in."""
        frame = _ohlcv_only()

        out = cls().calculate(frame)

        assert len(out) == len(frame)
        assert out.isna().all(), (
            f"{cls.__name__} fabricated values {out.dropna().unique().tolist()} "
            f"for an absent {column} column"
        )

    @pytest.mark.parametrize("cls,column", _INDICATORS)
    def test_output_index_follows_input(self, cls: type, column: str) -> None:
        """The NaN series still aligns with the caller's index."""
        frame = _ohlcv_only()
        frame.index = pd.date_range("2026-01-01", periods=len(frame), freq="h")

        assert cls().calculate(frame).index.equals(frame.index)

    @pytest.mark.parametrize("cls,column", _INDICATORS)
    def test_present_column_is_passed_through(self, cls: type, column: str) -> None:
        """A populated column still reads through unchanged."""
        frame = _ohlcv_only()
        frame[column] = np.linspace(-0.001, 0.001, len(frame))

        out = cls().calculate(frame)

        np.testing.assert_allclose(out.to_numpy(), frame[column].to_numpy())

    def test_neutral_zero_is_no_longer_indistinguishable_from_absent(self) -> None:
        """Regression: a real 0.0 reading and a missing column must differ.

        Before the fix both produced an all-zero series, so a strategy that
        (wrongly) received no data looked exactly like one reading a calm
        market.
        """
        with_data = _ohlcv_only()
        with_data["FUNDING_RATE"] = 0.0
        without_data = _ohlcv_only()

        from_data = FundingRateIndicator().calculate(with_data)
        from_absence = FundingRateIndicator().calculate(without_data)

        assert (from_data == 0.0).all()
        assert from_absence.isna().all()
        assert not from_data.equals(from_absence)

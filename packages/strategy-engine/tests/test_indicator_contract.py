"""Cross-indicator invariant suite.

One shared harness runs every registered indicator through the same set
of contracts, so a new indicator inherits the checks instead of needing
its own bespoke coverage. Each test is written against the whole
registry, not a hand-picked subset — the point is that a newly added
indicator cannot quietly skip them.

Contracts covered:
* output aligns with the input index (no accidental RangeIndex);
* the input frame is not mutated;
* a newest-first frame is refused rather than silently miscalculated;
* the same input yields the same output twice (determinism).
"""

from __future__ import annotations

import inspect
from typing import Any

import numpy as np
import pandas as pd
import pytest

from selene_space_strategy.indicators.base import (
    Indicator,
    reject_descending_index,
)
from selene_space_strategy.indicators.registry import indicator_registry

_RNG = np.random.default_rng(20261005)
_N = 120
_CLOSES = 300.0 + np.cumsum(_RNG.standard_normal(_N))
_HIGHS = _CLOSES + np.abs(_RNG.standard_normal(_N)) * 0.5
_LOWS = _CLOSES - np.abs(_RNG.standard_normal(_N)) * 0.5
_OPENS = np.roll(_CLOSES, 1)
_OPENS[0] = _CLOSES[0]
_VOLUMES = np.abs(_RNG.standard_normal(_N)) * 1000 + 100


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": _OPENS,
            "high": _HIGHS,
            "low": _LOWS,
            "close": _CLOSES,
            "volume": _VOLUMES,
            "FUNDING_RATE": _RNG.standard_normal(_N) * 0.01,
            "FUNDING_RATE_Z": _RNG.standard_normal(_N),
        }
    )


def _construct(name: str) -> Indicator:
    """Instantiate a registered indicator with usable defaults.

    Required constructor parameters are supplied a small positive lookback;
    any indicator needing something more exotic is skipped by the caller
    rather than force-fed a fabricated argument.
    """
    cls = indicator_registry.get(name)
    kwargs: dict[str, Any] = {}
    for pname, param in inspect.signature(cls.__init__).parameters.items():
        if pname == "self" or param.default is not inspect.Parameter.empty:
            continue
        if pname in ("period", "window", "max_lag", "lookback"):
            kwargs[pname] = 10
        else:
            pytest.skip(f"{name}: constructor needs {pname!r}, not auto-fillable")
    return cls(**kwargs)


ALL_INDICATORS = indicator_registry.list()


@pytest.mark.parametrize("name", ALL_INDICATORS)
class TestIndicatorInvariants:
    """Every registered indicator upholds the base-class contract."""

    def test_output_follows_input_index(self, name: str) -> None:
        """A non-default index must survive to the output untouched.

        An indicator that builds its result on a fresh RangeIndex lines up
        with the prices only by luck; against a timestamped frame it
        misaligns every row.
        """
        indicator = _construct(name)
        frame = _frame()
        frame.index = pd.RangeIndex(5_000, 5_000 + len(frame))

        out = indicator.calculate(frame)

        if isinstance(out, pd.DataFrame):
            assert out.index.equals(
                frame.index
            ), f"{name}: DataFrame output index diverged from input"
        else:
            assert out.index.equals(
                frame.index
            ), f"{name}: output index diverged from input"

    def test_input_frame_not_mutated(self, name: str) -> None:
        """calculate() is documented read-only; the caller's frame survives."""
        indicator = _construct(name)
        frame = _frame()
        before = frame.copy(deep=True)

        indicator.calculate(frame)

        pd.testing.assert_frame_equal(frame, before)

    def test_newest_first_frame_is_refused(self, name: str) -> None:
        """Descending input raises instead of returning wrong values.

        Exchange-native frames (OKX, and ccxt wrappers around it) carry a
        descending timestamp index. The rows are reversed and the index
        runs newest-first, which is exactly what the guard keys on.
        """
        indicator = _construct(name)
        descending = _frame()
        descending.index = pd.date_range(
            "2026-01-01", periods=len(descending), freq="h"
        )[::-1]

        with pytest.raises(ValueError, match="chronological"):
            indicator.calculate(descending)

    def test_reversed_rows_with_reset_index_are_indistinguishable(
        self, name: str
    ) -> None:
        """Documents the guard's one blind spot, so nobody assumes otherwise.

        Reversing the rows *and* resetting the index leaves an ascending
        RangeIndex — the ordering information is gone, so no guard can
        detect it from the frame alone. The contract here is only that the
        call does not raise; correctness upstream depends on the caller
        not discarding the index.
        """
        indicator = _construct(name)
        ambiguous = _frame().iloc[::-1].reset_index(drop=True)

        indicator.calculate(ambiguous)  # must not raise

    def test_repeated_calls_agree(self, name: str) -> None:
        """Same frame twice yields identical output (no hidden state drift)."""
        indicator = _construct(name)
        frame = _frame()

        first = indicator.calculate(frame)
        second = indicator.calculate(frame)

        if isinstance(first, pd.DataFrame):
            pd.testing.assert_frame_equal(first, second)
        else:
            pd.testing.assert_series_equal(first, second)


class TestChronologyGuard:
    """The guard itself, independent of any particular indicator."""

    def test_increasing_index_passes(self) -> None:
        reject_descending_index(pd.DataFrame({"close": [1.0, 2.0]}))

    def test_descending_index_raises(self) -> None:
        frame = _frame()
        frame.index = pd.RangeIndex(len(frame))[::-1]
        with pytest.raises(ValueError, match="chronological"):
            reject_descending_index(frame)

    def test_unsorted_index_is_not_flagged(self) -> None:
        """An unordered index is not the newest-first signature."""
        frame = _frame()
        frame.index = [1, 0] + list(range(2, len(frame)))
        reject_descending_index(frame)

    def test_non_frame_input_ignored(self) -> None:
        reject_descending_index(np.arange(5))

    def test_single_row_frame_ignored(self) -> None:
        reject_descending_index(pd.DataFrame({"close": [1.0]}))

    def test_guard_is_wired_into_every_subclass(self) -> None:
        """Subclasses are wrapped at class-creation time, not by convention."""
        for name in ALL_INDICATORS:
            cls = indicator_registry.get(name)
            assert getattr(
                cls.calculate, "_chronology_guarded", False
            ), f"{name}: calculate() is not chronology-guarded"

    def test_calculate_full_is_guarded_too(self) -> None:
        """The richer variants accept a frame, so they need the guard as well.

        Guarding only ``calculate`` would leave MACD's trio, Bollinger
        bands, and the stochastic pair free to read reversed data.
        """

        full_variants = [
            name
            for name in ALL_INDICATORS
            if "calculate_full" in indicator_registry.get(name).__dict__
        ]
        assert full_variants, "expected at least one calculate_full variant"

        for name in full_variants:
            cls = indicator_registry.get(name)
            assert getattr(
                getattr(cls, "calculate_full"), "_chronology_guarded", False
            ), f"{name}: calculate_full() is not chronology-guarded"

    def test_calculate_full_refuses_descending_frame(self) -> None:
        """End-to-end: a reversed frame raises on the full variant too."""
        descending = _frame()
        descending.index = pd.date_range(
            "2026-01-01", periods=len(descending), freq="h"
        )[::-1]

        for name in ALL_INDICATORS:
            cls = indicator_registry.get(name)
            if "calculate_full" not in cls.__dict__:
                continue
            with pytest.raises(ValueError, match="chronological"):
                getattr(_construct(name), "calculate_full")(descending)

    def test_guarded_method_is_not_double_wrapped(self) -> None:
        """A subclass sharing the base implementation is wrapped exactly once."""

        def depth(func) -> int:
            n = 0
            while getattr(func, "_chronology_guarded", False):
                n += 1
                func = func.__wrapped__  # type: ignore[attr-defined]
            return n

        for name in ALL_INDICATORS:
            cls = indicator_registry.get(name)
            assert (
                depth(cls.calculate) == 1
            ), f"{name}: calculate wrapped {depth(cls.calculate)}x"
            if "calculate_full" in cls.__dict__:
                full = getattr(cls, "calculate_full")
                assert (
                    depth(full) == 1
                ), f"{name}: calculate_full wrapped {depth(full)}x"

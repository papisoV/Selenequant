"""Indicator ABC shared by every technical indicator in the package.

Concrete indicators implement :attr:`name`, report their dominant
lookback via :attr:`period`, and evaluate a price frame through
:meth:`calculate`.
"""

from __future__ import annotations

import functools
from abc import ABC, abstractmethod

import pandas as pd


def _checked_window(window: int) -> int:
    """Reject non-positive lookback windows, passing valid ones through."""
    if window <= 0:
        raise ValueError(f"Period must be positive, got {window}")
    return window


def reject_descending_index(data: object) -> None:
    """Refuse a frame whose index runs newest-first.

    Every indicator here reads its input top-to-bottom as oldest-to-newest
    (:class:`Indicator` documents that contract). A caller that hands over
    an exchange-native, newest-first frame — OKX returns klines that way,
    and this project has already been bitten by the resulting silently
    wrong values — gets a computed series that is wrong everywhere, with
    no exception and no NaN to hint at it.

    Detecting the mistake needs an ordered index, so the check is limited
    to frames that carry one: a strictly decreasing index is the
    newest-first signature and raises, while an increasing or unordered
    index passes through. A constant index counts as neither (an index of
    equal timestamps says nothing about direction) and is left alone.

    Args:
        data: Candidate input; non-frame values are ignored so helper
            call sites that pass arrays stay unaffected.

    Raises:
        ValueError: if ``data`` is a DataFrame whose index strictly
            decreases.
    """
    if not isinstance(data, pd.DataFrame):
        return
    index = data.index
    if (
        len(index) > 1
        and index.is_monotonic_decreasing
        and not index.is_monotonic_increasing
    ):
        raise ValueError(
            "Indicator input must be chronological (oldest row first), but the "
            "frame index decreases. Reverse the rows (or sort by the time "
            "column) before calling, e.g. frame.iloc[::-1] for exchange-native "
            "newest-first data."
        )


def _guard_chronology(cls: type, method_name: str) -> None:
    """Wrap ``cls.method_name`` so a newest-first frame raises.

    Applied at class-creation time for both ``calculate`` and, when a
    subclass defines one, ``calculate_full`` — the two entry points that
    accept a frame. Guarding only ``calculate`` would leave the richer
    variants (MACD's line/signal/histogram trio, Bollinger bands, the
    stochastic pair) free to consume reversed data silently.

    A method already wrapped is left alone, so the base class and any
    subclass sharing one implementation are not double-wrapped.
    """
    raw = cls.__dict__.get(method_name)
    if raw is None or getattr(raw, "_chronology_guarded", False):
        return

    @functools.wraps(raw)
    def guarded(self, data, *args, **kwargs):  # type: ignore[no-untyped-def]
        reject_descending_index(data)
        return raw(self, data, *args, **kwargs)

    guarded._chronology_guarded = True  # type: ignore[attr-defined]
    setattr(cls, method_name, guarded)


class Indicator(ABC):
    """Common ancestor of all indicators with a validated lookback window.

    Input frames arrive oldest row first and always carry ``"close"``;
    range- or volume-aware implementations additionally read ``"high"``,
    ``"low"``, ``"open"`` and/or ``"volume"``. Output rows sit on the
    input index one-for-one, and the stretch before the lookback fills
    reads NaN instead of half-fed estimates. That policy is what makes
    chaining honest: dropping one indicator's output into another frame
    as a column composes without hidden warm-up artifacts.

    The chronological half of that contract is enforced for every
    subclass rather than left to each implementation: :meth:`calculate`
    is wrapped at class-creation time so a newest-first frame raises
    instead of yielding a plausible-looking wrong answer.
    """

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        _guard_chronology(cls, "calculate")
        _guard_chronology(cls, "calculate_full")

    def __init__(self, period: int) -> None:
        self._period = _checked_window(period)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(period={self._period})"

    @property
    def period(self) -> int:
        """Lookback length."""
        return self._period

    @property
    @abstractmethod
    def name(self) -> str:
        """Registry identifier ('sma', 'rsi', ...)."""
        ...

    @abstractmethod
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Evaluate this indicator across ``data``.

        The frame is consumed read-only and expected chronological.
        Whatever comes back lines up with ``data.index`` row for row,
        opening NaN until enough bars exist to evaluate every window.
        """
        ...

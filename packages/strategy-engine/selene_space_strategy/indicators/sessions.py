"""Session / killzone detectors (ICT time-of-day concepts).

Inner-Circle-Trader assigns different behaviour to different hours of the
trading day: the London and New York *killzones* are the windows where
liquidity is taken and the day's direction is decided, while the hours
between them are where positions bleed.

This module turns those windows into a causal signal series compatible with
every other indicator here: ``+1`` on a bar inside an enabled killzone, ``0``
on a bar outside one.

**Reading the clock.** The signal needs wall-clock time, which sets this
detector apart from every other indicator in the package (all of them are
index-agnostic). Two sources are accepted, in this order:

1. a :class:`~pandas.DatetimeIndex` on the frame — the case for research
   frames built straight from a timestamped CSV or database query;
2. a ``timestamp`` column holding epoch milliseconds — the case for the
   :class:`~selene_space_strategy.runtime.StrategyRuntime`, whose
   ``_indicator_frame`` builds a plain ``RangeIndex`` frame from the bar
   dicts and carries time only in that column.

When neither exists the detector has no clock. It then emits all-NaN rather
than an all-zero series: ``0`` would assert "never in a killzone", which is a
claim about the market, while NaN says truthfully "this frame can't be
clocked". Under the runtime's new-row gate an all-NaN column blocks entries
instead of silently trading a strategy that never fires, and
:attr:`KillzoneIndicator.clock_source` reports ``None`` so a caller can tell
the two cases apart. Frame shapes that look clockable but aren't — a
``timestamp`` column of unparseable or out-of-range numbers — also fall back
to NaN, never to a guessed unit.

Session boundaries are expressed in the detector's local clock. A caller
using the ICT convention of "New York local time" should pass
``tz_offset_hours=-5`` (or ``-4`` during daylight saving) rather than
pre-shifting the timestamps, so the offset stays a visible, tunable parameter
instead of being baked into the data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator


# ---------------------------------------------------------------------------
# Session definitions
# ---------------------------------------------------------------------------

#: Killzone windows as ``(start_hour, end_hour)`` in the detector's local
#: clock, end exclusive. Values are the ICT conventions measured in New York
#: local time; a frame clocked in UTC with the default ``tz_offset_hours=-5``
#: sees London as 02:00-05:00 and New York as 07:00-10:00.
KILLZONES: dict[str, tuple[int, int]] = {
    "london": (2, 5),
    "new_york": (7, 10),
    "asia": (20, 24),
}

# Epoch magnitude floors used to infer a numeric timestamp's unit. Anything
# below the seconds floor is not a plausible epoch timestamp (it is more
# likely a bar ordinal or a broken column), so it is treated as no clock.
# The ceiling sits above year 3000 in milliseconds: beyond it the value is not
# a timestamp we can honour, and int64 conversion would silently wrap.
_MIN_EPOCH_SECONDS = 1_000_000_000
_MIN_EPOCH_MILLIS = 100_000_000_000
_MAX_EPOCH_MILLIS = 32_503_680_000_000


@dataclass
class KillzoneWindow:
    """One resolved killzone in local-clock hours."""

    name: str
    start_hour: int
    end_hour: int

    def contains(self, hour: int) -> bool:
        """True when ``hour`` (0-23) falls inside the window."""
        if self.start_hour <= self.end_hour:
            return self.start_hour <= hour < self.end_hour
        # A window that wraps past midnight (e.g. 22 -> 02).
        return hour >= self.start_hour or hour < self.end_hour

    def __str__(self) -> str:
        return f"{self.name}[{self.start_hour:02d}:00-{self.end_hour:02d}:00)"


def _coerce_to_utc_naive(stamps: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Normalise a datetime index to tz-naive UTC so a manual offset is exact."""
    if stamps.tz is not None:
        return stamps.tz_convert("UTC").tz_localize(None)
    return stamps


def _stamps_from_column(values: pd.Series) -> pd.DatetimeIndex | None:
    """Best-effort epoch conversion of a ``timestamp`` column, else None.

    A datetime-typed column is used as-is. A numeric column has its unit
    inferred from magnitude and converted; anything else — object dtype
    holding strings, NaNs, a sub-second-floor ordinal, or a magnitude outside
    the sane epoch window — returns None, because guessing a unit there would
    fabricate a time-of-day.
    """
    if pd.api.types.is_datetime64_any_dtype(values):
        stamps = pd.DatetimeIndex(values.to_numpy())
        # NaT rows would otherwise read as "outside every window" (their
        # ``.hour`` is NaN and comparisons fail closed), turning a missing
        # clock into a silent no-trade filter. Reject the column instead.
        if stamps.hasnans:
            return None
        return stamps

    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.isna().any():
        return None
    # Bound the magnitude *before* going through int64: a value such as 1e20
    # wraps to a negative int64 and converts to a plausible-looking ancient
    # date, which reads as "outside the window" for every bar.
    magnitude = float(numeric.abs().max())
    if magnitude >= _MAX_EPOCH_MILLIS:
        return None
    if magnitude >= _MIN_EPOCH_MILLIS:
        unit = "ms"
    elif magnitude >= _MIN_EPOCH_SECONDS:
        unit = "s"
    else:
        return None
    try:
        converted = pd.to_datetime(
            numeric.astype("int64").to_numpy(), unit=unit, utc=True
        )
    except (ValueError, OverflowError, pd.errors.OutOfBoundsDatetime):
        # Micro-/nanosecond epochs land far outside pandas' range. Returning
        # None keeps the contract (all-NaN, no entries) instead of raising out
        # of the warm-up path, which has no exception guard.
        return None
    return pd.DatetimeIndex(converted)


def _clock_hours(data: pd.DataFrame, tz_offset_hours: int) -> pd.Series | None:
    """Hour-of-day in the detector's local clock, or None when unclockable.

    The offset is applied by hand rather than through ``tz_convert`` so a
    tz-naive index (the common case — klines store epoch milliseconds) works
    the same as a tz-aware one, and so fractional offsets such as India's
    +5:30 collapse to whole hours predictably.

    The returned series is keyed by ``data``'s own index so it lines up row
    for row, as the :class:`Indicator` contract requires.
    """
    if isinstance(data.index, pd.DatetimeIndex):
        stamps: pd.DatetimeIndex | None = data.index
    elif "timestamp" in data.columns:
        stamps = _stamps_from_column(data["timestamp"])
    else:
        stamps = None

    if stamps is None or len(stamps) != len(data):
        return None

    shifted = _coerce_to_utc_naive(stamps) + pd.Timedelta(hours=tz_offset_hours)
    return pd.Series(shifted.hour, index=data.index)


class KillzoneIndicator(Indicator):
    """Series: +1 inside an enabled killzone, 0 outside, NaN when unclockable.

    ``sessions`` selects which windows count. The default enables London and
    New York (the two the ICT literature treats as tradable) and leaves Asia
    out, because the Asia session is where the range that later gets raided
    is built rather than where the raid happens.

    The signal is level, not event: it is +1 on *every* bar inside the
    window, so a strategy pairs it with a structure condition (FVG, sweep,
    order block) rather than treating +1 as an entry by itself.
    """

    def __init__(
        self,
        period: int = 1,
        sessions: str = "london,new_york",
        tz_offset_hours: int = -5,
    ) -> None:
        """``sessions`` is a comma-separated list of keys from :data:`KILLZONES`;
        ``tz_offset_hours`` shifts the frame's clock into the session's local
        time."""
        super().__init__(period)
        names = [s.strip().lower() for s in str(sessions).split(",") if s.strip()]
        unknown = [n for n in names if n not in KILLZONES]
        if unknown:
            raise ValueError(
                f"Unknown killzone session(s) {unknown}. Available: {sorted(KILLZONES)}"
            )
        if not names:
            raise ValueError("At least one killzone session must be enabled.")
        self._windows = [
            KillzoneWindow(name=n, start_hour=KILLZONES[n][0], end_hour=KILLZONES[n][1])
            for n in names
        ]
        self._tz_offset_hours = int(tz_offset_hours)
        self._clock_source: str | None = None

    @property
    def name(self) -> str:
        return "killzone"

    @property
    def windows(self) -> list[KillzoneWindow]:
        """Resolved windows, for callers that want to report them."""
        return list(self._windows)

    @property
    def clock_source(self) -> str | None:
        """Where the last ``calculate`` read time from.

        ``"index"`` for a DatetimeIndex, ``"timestamp"`` for the column, and
        ``None`` when the frame carried no usable clock and every row came
        back NaN.
        """
        return self._clock_source

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """+1 inside an enabled killzone, 0 outside, NaN without a clock."""
        hours = _clock_hours(data, self._tz_offset_hours)
        if hours is None:
            self._clock_source = None
            return pd.Series(np.nan, index=data.index, dtype=float)

        self._clock_source = (
            "index" if isinstance(data.index, pd.DatetimeIndex) else "timestamp"
        )
        inside = pd.Series(False, index=data.index)
        for window in self._windows:
            inside |= hours.map(window.contains)
        return inside.astype(float)

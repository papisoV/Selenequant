"""Volatility envelope around a central moving average."""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.indicators.base import Indicator


class BollingerBandsResult:
    """The three-band bundle.

    Attributes:
        upper: spine plus one scaled-deviation term.
        middle: the windowed mean of the close itself.
        lower: spine minus the same term.
    """

    __slots__ = ("lower", "middle", "upper")

    def __init__(self, upper: pd.Series, middle: pd.Series, lower: pd.Series) -> None:
        self.upper = upper
        self.middle = middle
        self.lower = lower


class BollingerBands(Indicator):
    """John Bollinger's volatility envelope, 1980s vintage.

    Three tracks come out of one construction:

    1. A ``period``-bar trailing mean of the close — the spine the whole
       structure hangs from.
    2. The sample standard deviation of those same ``period`` closes,
       scaled by ``num_std``.
    3. Upper and lower rails parked ``num_std`` deviations above and
       below the spine.

    Textbook defaults are 20 bars at 2x. Band width breathes with
    volatility: quiet stretches squeeze the rails together ("the
    squeeze"), violent moves blow them apart. Price walking outside a
    rail is a deviation event, not automatically a reversal signal.

    Undefined until a complete window exists — every series starts NaN.

    Bit-compatibility detail: the deviation uses pandas'
    ``rolling(...).std()`` (sample form, ddof=1) and the window sums run
    through pandas' own kernels, so all three tracks reproduce
    ``df.close.rolling(20).mean() ± std * mult`` exactly.
    """

    def _spine_and_width(self, data: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """Central mean plus its scaled dispersion term.

        Both aggregations share one kwargs dict to guarantee identical
        windowing between mean and standard deviation — a mismatch there
        would skew the rails without any error raised.
        """
        windowing = dict(min_periods=self._period, window=self._period)
        quotes = data["close"]
        spine = quotes.rolling(**windowing).mean()
        width = quotes.rolling(**windowing).std() * self._num_std
        return spine, width

    def __init__(self, period: int = 20, num_std: float = 2.0) -> None:
        super().__init__(period)
        self._num_std = num_std

    @property
    def name(self) -> str:
        return "bbands"

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Return just the central band.

        Args:
            data: Frame exposing ``"close"``.

        Returns:
            Trailing mean of the close; NaN ahead of bar ``period``.
        """
        return data["close"].rolling(self._period, min_periods=self._period).mean()

    def calculate_full(self, data: pd.DataFrame) -> BollingerBandsResult:
        """Assemble the complete three-track envelope.

        Args:
            data: Frame exposing ``"close"``.

        Returns:
            A :class:`BollingerBandsResult` whose rails are computed as
            spine-plus-width and spine-minus-width.
        """
        spine, width = self._spine_and_width(data)
        return BollingerBandsResult(
            upper=spine + width,
            middle=spine,
            lower=spine - width,
        )

    def __repr__(self) -> str:
        return f"BollingerBands(period={self._period}, std={self._num_std})"

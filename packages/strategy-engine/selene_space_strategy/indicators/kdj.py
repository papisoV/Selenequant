"""KDJ — the J-amplified stochastic favoured on Chinese exchanges."""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.base import Indicator


class KDJResult:
    """Carrier for the K, D and J tracks.

    Attributes:
        k: single-smoothed raw stochastic value.
        d: ``k`` averaged once more — the slower of the pair.
        j: divergence amplifier, ``3k - 2d``; routinely leaves the 0-100
            band because it extrapolates the gap between the two parents.
    """

    __slots__ = ("d", "j", "k")

    def __init__(self, k: pd.Series, d: pd.Series, j: pd.Series) -> None:
        self.k = k
        self.d = d
        self.j = j


class KDJ(Indicator):
    """KDJ in its standard exchange-house form.

    Four steps from OHLC to the trio:

    1. Scan a N-bar envelope: rolling max of highs, rolling min of lows.
    2. RSV — raw stochastic value — is how far the close sits above the
       floor as a percentage of the corridor.
    3. Two chained short means: RSV -> K (one pass), K -> D (another).
       Both passes share the same width, ``smooth_period``.
    4. J stretches the K/D separation: ``J = 3K - 2D``, which reacts
       earlier than either parent at the cost of overshooting.

    The customary desk setup scans 9 bars and smooths over 3. Where the
    plain Stochastic tolerates partial envelopes, this one does not:
    every window must fill completely before any value appears, and the
    published table stays NaN until bar ``period + 2*smooth_period - 1``
    has closed.
    """

    def __init__(self, period: int = 9, smooth_period: int = 3) -> None:
        """Configure envelope length and shared smoothing width.

        Args:
            period: Bars scanned per raw stochastic reading.
            smooth_window: Width used twice — RSV to K, then K to D.
        """
        super().__init__(period)
        if smooth_period <= 0:
            raise ValueError(f"Smooth period must be positive, got {smooth_period}")
        self._smooth_period = smooth_period

    @property
    def name(self) -> str:
        return "kdj"

    @property
    def smooth_period(self) -> int:
        """One width reused by both smoothing passes."""
        return self._smooth_period

    def _rsv(self, data: pd.DataFrame) -> pd.Series:
        """Raw stochastic value: close's percent height in the envelope.

        Rows whose envelope has collapsed flat divide zero by zero and
        stay NaN — no special-casing needed beyond letting IEEE do its
        thing.
        """
        ceiling = data["high"].rolling(window=self._period).max()
        floor = data["low"].rolling(window=self._period).min()
        corridor = ceiling - floor
        return (data["close"] - floor) / corridor * 100.0

    @staticmethod
    def _amplify(k_line: pd.Series, d_line: pd.Series) -> pd.Series:
        """J line: exaggerate the K/D spread three-to-two."""
        return 3.0 * k_line - 2.0 * d_line

    def _assemble(self, k_line: pd.Series, d_line: pd.Series, j_line: pd.Series, index) -> pd.DataFrame:
        """Bundle the trio into one frame and blank the warm-up rows."""
        warmup = self._period + 2 * self._smooth_period - 1
        bundle = pd.DataFrame({"k": k_line, "d": d_line, "j": j_line}, index=index)
        bundle.iloc[:warmup] = np.nan
        return bundle

    def calculate(self, data: pd.DataFrame) -> pd.DataFrame:
        """Return columns ``"k"``, ``"d"``, ``"j"``.

        Args:
            data: Frame exposing ``"high"``, ``"low"``, ``"close"``.

        Returns:
            Frame aligned to ``data.index``; every column reads NaN until
            the envelope plus both smoothing passes have completed.
        """
        rsv = self._rsv(data)
        fast = rsv.rolling(window=self._smooth_period).mean()
        slow = fast.rolling(window=self._smooth_period).mean()
        lead = self._amplify(fast, slow)
        return self._assemble(fast, slow, lead, data.index)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(period={self._period}, smooth_period={self._smooth_period})"

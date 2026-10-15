"""Relative Strength Index with Wilder's gain/loss smoothing."""

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


class RSI(Indicator):
    """Relative Strength Index (Welles Wilder, 1978).

    Pipeline: split bar-to-bar close changes into up-steps and down-steps,
    smooth each side with Wilder's recursion (exponential weight ``1 /
    period``), then compress the ratio into 0-100:

        RS = avg_up / avg_down
        RSI = 100 - 100 / (1 + RS)

    Matches TradingView's ``ta.rsi(close, length)``.

    ``mode`` does not touch the math. ``"standard"`` reads >70 as overbought
    and <30 as oversold; ``"trend"`` reflects research on crypto markets
    where >70 marks trend continuation instead of exhaustion — pair it with
    an 81-bar window (finding from a 6-year BTC study). It exists so
    consuming code can select the interpretation explicitly.
    """

    def __init__(self, period: int = 14, mode: str = "standard") -> None:
        """Create an RSI indicator.

        Args:
            period: Lookback for the gain/loss averages; 81 pairs with
                ``mode="trend"``.
            mode: ``"standard"`` or ``"trend"`` — interpretation only.
        """
        super().__init__(period)
        if mode not in ("standard", "trend"):
            raise ValueError(f"mode must be 'standard' or 'trend', got {mode!r}")
        self._mode = mode

    @property
    def name(self) -> str:
        return "rsi"

    @property
    def mode(self) -> str:
        """Selected reading convention ('standard' or 'trend')."""
        return self._mode

    def _running_means(self, steps: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Wilder-smooth the up-steps and down-steps in parallel.

        Bit-matched to smoothing each side with ``ewm(adjust=False)``:
        the residual weight from the previous step decays once per bar,
        a step equal to the running mean bypasses the update, and the
        weight ledger resets after every observed step. Missing closes
        yield a NaN step, which falls through both sign tests into plain
        zeros on each side — so the averages march straight through data
        holes with no special resume logic.
        """
        weight = _wilder_weight(self._period)
        fading = 1.0 - weight
        count = len(steps)
        mean_up = np.empty(count)
        mean_down = np.empty(count)
        run_up: float = steps[0] if steps[0] > 0.0 else 0.0
        run_down: float = -steps[0] if steps[0] < 0.0 else 0.0
        mean_up[0] = run_up
        mean_down[0] = run_down
        residue = 1.0
        for pos in range(1, count):
            step = steps[pos]
            obs_up = step if step > 0.0 else 0.0
            obs_down = -step if step < 0.0 else 0.0
            residue *= fading
            if run_up != obs_up:
                run_up = (residue * run_up + weight * obs_up) / (residue + weight)
            if run_down != obs_down:
                run_down = (residue * run_down + weight * obs_down) / (residue + weight)
            residue = 1.0
            mean_up[pos] = run_up
            mean_down[pos] = run_down
        return mean_up, mean_down

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """Compute RSI over the close column.

        Args:
            data: Frame containing a ``"close"`` column.

        Returns:
            Series bounded to [0, 100]; the first ``period`` rows are NaN.
        """
        closes = data["close"].to_numpy(dtype="float64")

        # Bar-over-bar change with the leading entry zeroed.
        steps = np.diff(closes, prepend=closes[0])
        mean_up, mean_down = self._running_means(steps)

        # Where avg_down is exactly zero the ratio explodes to inf and the
        # compression below lands on 100 — matching the classic formula.
        with np.errstate(divide="ignore", invalid="ignore"):
            strength = mean_up / mean_down
            rsi_values = 100.0 - 100.0 / (1.0 + strength)

        rsi_values[: self._period] = np.nan
        return pd.Series(rsi_values, index=data.index, name="rsi")

"""CycleGate: quantified gate signals + hysteresis state machine.

Ported gate semantics from the user's grid experiment (``grid_sim.py``
@ 43eee46, CycleGate class, :435-490): stop requires a 2-consecutive streak
(configurable down to 1 for backtests via ``dangerous_stop_needs``), start
fires on the first vote, and any signal-source change resets the counters.

The LLM cycle diagnosis of the source experiment is replaced by pluggable
**quantified** signals (PRD principle: 量化指标替代 LLM 门控):

- ``ERGateSignal``   — strategy-engine Kaufman EfficiencyRatio, period=14.
  ER<0.25 (ranging churn) -> start; ER>0.6 (clean trend) -> stop.
- ``ADXGateSignal``  — strategy-engine Wilder ADX (same indicator alpha-core
  regime.py:55 reuses for its trending/ranging thresholds). ADX<20 -> start;
  ADX>30 -> stop.

Design history note: the original experiment's "ER-as-PA-stand-in" LLM
calibration FAILED 3/3 — see scripts/grid_calibrate.py header. ER/ADX gate
survival is decided by that script's data-driven rule, not preset here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd

from selene_space_strategy.indicators.adx import ADX
from selene_space_strategy.indicators.efficiency_ratio import EfficiencyRatio

STOP_NEEDS_CONSECUTIVE = 2
START_NEEDS_CONSECUTIVE = 1

ER_START_BELOW = 0.25
ER_STOP_ABOVE = 0.6
ER_PERIOD = 14

ADX_START_BELOW = 20.0
ADX_STOP_ABOVE = 30.0
ADX_PERIOD = 14


@dataclass(frozen=True)
class GateDecision:
    action: str  # start | stop | continue | observe
    value: float  # latest signal reading (ER or ADX), NaN when unavailable
    reason: str


@runtime_checkable
class GateSignal(Protocol):
    """Pluggable signal source: map a bar window to a gate vote."""

    name: str

    def vote(self, bars: pd.DataFrame) -> GateDecision:
        """Evaluate the most recent bar of *bars* (high/low/close columns)."""
        ...


class ERGateSignal:
    """Kaufman EfficiencyRatio gate: low ER = range (start), high ER = trend (stop)."""

    name = "er"

    def __init__(
        self,
        period: int = ER_PERIOD,
        start_below: float = ER_START_BELOW,
        stop_above: float = ER_STOP_ABOVE,
    ) -> None:
        self.period = period
        self.start_below = start_below
        self.stop_above = stop_above
        self._ind = EfficiencyRatio(period=period)

    def vote(self, bars: pd.DataFrame) -> GateDecision:
        er = self._ind.calculate(bars)
        value = float(er.iloc[-1]) if len(er) else float("nan")
        if value != value:  # NaN — not enough bars yet
            return GateDecision(
                "observe", value, f"ER warmup (<{self.period + 1} bars)"
            )
        if value < self.start_below:
            return GateDecision(
                "start", value, f"ER {value:.3f} < {self.start_below} (ranging)"
            )
        if value > self.stop_above:
            return GateDecision(
                "stop", value, f"ER {value:.3f} > {self.stop_above} (trending)"
            )
        return GateDecision("continue", value, f"ER {value:.3f} in neutral band")


class ADXGateSignal:
    """Wilder ADX gate: weak trend (start), strong trend (stop)."""

    name = "adx"

    def __init__(
        self,
        period: int = ADX_PERIOD,
        start_below: float = ADX_START_BELOW,
        stop_above: float = ADX_STOP_ABOVE,
    ) -> None:
        self.period = period
        self.start_below = start_below
        self.stop_above = stop_above
        self._ind = ADX(period=period)

    def vote(self, bars: pd.DataFrame) -> GateDecision:
        adx = self._ind.calculate(bars)
        value = float(adx.iloc[-1]) if len(adx) else float("nan")
        if value != value:
            return GateDecision(
                "observe", value, f"ADX warmup (<{2 * self.period} bars)"
            )
        if value < self.start_below:
            return GateDecision(
                "start", value, f"ADX {value:.1f} < {self.start_below} (weak trend)"
            )
        if value > self.stop_above:
            return GateDecision(
                "stop", value, f"ADX {value:.1f} > {self.stop_above} (strong trend)"
            )
        return GateDecision("continue", value, f"ADX {value:.1f} in neutral band")


class CycleGate:
    """Hysteresis state machine over a GateSignal (source grid_sim.py:435-490).

    - stop fires only after ``dangerous_stop_needs`` consecutive stop votes
      (default 2; backtests may pass 1)
    - start fires on the first start vote (``START_NEEDS_CONSECUTIVE``=1)
    - a change in the signal's underlying regime vote resets both counters
    - the grid owner translates actions: start -> (re)arm grid,
      stop -> waterfall exit, continue/observe -> hold
    """

    def __init__(
        self, signal: GateSignal, dangerous_stop_needs: int = STOP_NEEDS_CONSECUTIVE
    ) -> None:
        if dangerous_stop_needs < 1:
            raise ValueError("dangerous_stop_needs must be >= 1")
        self.signal = signal
        self.dangerous_stop_needs = dangerous_stop_needs
        self.stop_count = 0
        self.start_count = 0
        self.last_vote_kind: str | None = None
        self.started = False

    def evaluate(self, bars: pd.DataFrame) -> GateDecision:
        d = self.signal.vote(bars)
        kind = d.action

        if kind != self.last_vote_kind:
            # regime vote changed -> fresh streak (source: cycle change reset)
            self.stop_count = 0
            self.start_count = 0
        self.last_vote_kind = kind

        if kind == "stop":
            self.stop_count += 1
            self.start_count = 0
            action = (
                "stop" if self.stop_count >= self.dangerous_stop_needs else "continue"
            )
        elif kind == "start":
            self.start_count += 1
            self.stop_count = 0
            action = (
                "start" if self.start_count >= START_NEEDS_CONSECUTIVE else "observe"
            )
        else:  # continue / observe
            self.stop_count = 0
            self.start_count = 0
            action = kind

        if action == "start":
            self.started = True
        elif action == "stop":
            self.started = False
        return GateDecision(action=action, value=d.value, reason=d.reason)

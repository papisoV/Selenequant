"""CycleGate synthetic-sample tests (US-5).

Fixed fixture written into this file: 40 bars of flat oscillation followed
by 20 bars of a clean directional trend, ER period fixed at 14. Assertions:
  - oscillation segment keeps the gate started
  - trend segment stops within <= 2 bars of the trend start
  - a single high-ER glitch bar inside oscillation does not trigger stop
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from selene_space_grid.cycle_gate import (
    ADXGateSignal,
    CycleGate,
    ERGateSignal,
    GateDecision,
    GateSignal,
)

# ── synthetic fixture (fixed, deterministic) ────────────────────────────────


def _oscillation_bars(
    n: int = 40, base: float = 100.0, amp: float = 1.0, start_ts: int = 1_000_000
) -> list[dict]:
    """Flat oscillation: close alternates +amp/-amp around base."""
    bars = []
    for i in range(n):
        close = base + amp if i % 2 == 0 else base - amp
        bars.append(
            {
                "time": start_ts + i,
                "open": base,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1.0,
            }
        )
    return bars


def _trend_bars(
    n: int = 20, start: float = 100.0, step: float = 20.0, start_ts: int = 1_000_000
) -> list[dict]:
    """Clean steep uptrend: close rises `step` per bar (steep enough that a
    14-bar ER window crosses 0.6 within 2 bars of trend start)."""
    bars = []
    for i in range(n):
        close = start + step * (i + 1)
        bars.append(
            {
                "time": start_ts + i,
                "open": start + step * i,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1.0,
            }
        )
    return bars


def _glitch_oscillation_bars(n: int = 40, glitch_idx: int = 30) -> list[dict]:
    """Oscillation with ONE trend-shaped bar inserted at glitch_idx."""
    bars = _oscillation_bars(n)
    base = 100.0
    j = glitch_idx
    close = base + 4.0  # one strong directional bar only
    bars[j] = {
        "time": bars[j]["time"],
        "open": base,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": 1.0,
    }
    return bars


def _df(bars: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(bars)


# ── hysteresis state machine (signal-independent) ───────────────────────────


class _FakeSignal:
    """Scripted GateSignal for state-machine tests."""

    name = "fake"

    def __init__(self, votes: list[str]) -> None:
        self.votes = list(votes)
        self.i = 0

    def vote(self, bars: pd.DataFrame) -> GateDecision:
        action = self.votes[min(self.i, len(self.votes) - 1)]
        self.i += 1
        return GateDecision(action=action, value=0.0, reason="scripted")


class TestCycleGateStateMachine:
    def test_stop_requires_two_consecutive(self):
        gate = CycleGate(_FakeSignal(["stop"]))
        assert gate.evaluate(_df([{}])).action == "continue"
        assert gate.evaluate(_df([{}])).action == "stop"

    def test_stop_respects_dangerous_stop_needs_one(self):
        gate = CycleGate(_FakeSignal(["stop"]), dangerous_stop_needs=1)
        assert gate.evaluate(_df([{}])).action == "stop"

    def test_start_fires_on_first_vote(self):
        gate = CycleGate(_FakeSignal(["start"]))
        assert gate.evaluate(_df([{}])).action == "start"

    def test_vote_change_resets_streaks(self):
        gate = CycleGate(_FakeSignal(["stop", "start", "stop"]))
        gate.evaluate(_df([{}]))  # stop streak: 1
        gate.evaluate(_df([{}]))  # start vote resets counters
        d = gate.evaluate(_df([{}]))  # fresh stop streak: 1 -> continue
        assert d.action == "continue"

    def test_started_flag_tracks_lifecycle(self):
        gate = CycleGate(_FakeSignal(["start", "stop", "stop"]))
        gate.evaluate(_df([{}]))
        assert gate.started is True
        gate.evaluate(_df([{}]))  # first stop -> continue (hysteresis)
        assert gate.started is True
        gate.evaluate(_df([{}]))  # second stop -> stop
        assert gate.started is False

    def test_dangerous_stop_needs_zero_rejected(self):
        with pytest.raises(ValueError):
            CycleGate(_FakeSignal([]), dangerous_stop_needs=0)


# ── ER gate on the synthetic fixture ────────────────────────────────────────


class TestERGateSynthetic:
    def test_oscillation_keeps_started(self):
        bars = _oscillation_bars(40)
        gate = CycleGate(ERGateSignal(period=14))
        actions = [gate.evaluate(_df(bars[: i + 1])).action for i in range(len(bars))]
        # after warmup the flat churn must produce start (or neutral continue)
        # and never a stop
        tail = actions[20:]
        assert all(a != "stop" for a in tail), f"oscillation stopped: {tail}"
        assert gate.started is True

    def test_trend_stops_within_two_bars(self):
        bars = _oscillation_bars(40) + _trend_bars(20)
        gate = CycleGate(ERGateSignal(period=14))
        stop_at = None
        for i in range(len(bars)):
            d = gate.evaluate(_df(bars[: i + 1]))
            if d.action == "stop" and stop_at is None:
                stop_at = i
        assert stop_at is not None, "trend never stopped the gate"
        assert stop_at <= 40 + 2, f"stop at {stop_at}, expected <= 42"

    def test_single_glitch_does_not_trigger_stop(self):
        bars = _glitch_oscillation_bars(40, glitch_idx=30)
        gate = CycleGate(ERGateSignal(period=14))
        actions = [gate.evaluate(_df(bars[: i + 1])).action for i in range(len(bars))]
        # a single trend-shaped bar gives one stop vote; hysteresis must
        # surface it as continue (and the next oscillation bar resets the streak)
        assert all(a != "stop" for a in actions), f"glitch fired stop: {actions}"

    def test_ergate_value_warmup_observe(self):
        gate = CycleGate(ERGateSignal(period=14))
        d = gate.evaluate(_df(_oscillation_bars(5)))
        assert d.action == "observe"
        assert math.isnan(d.value)


# ── ADX gate on the synthetic fixture ───────────────────────────────────────


class TestADXGateSynthetic:
    def test_oscillation_keeps_started(self):
        bars = _oscillation_bars(40)
        gate = CycleGate(ADXGateSignal(period=14))
        actions = [gate.evaluate(_df(bars[: i + 1])).action for i in range(len(bars))]
        tail = actions[30:]  # ADX needs ~2*period warmup
        assert all(a != "stop" for a in tail), f"oscillation stopped: {tail}"

    def test_trend_stops_within_six_bars(self):
        # ADX is Wilder-smoothed: inherent lag from <20 to >30 spans several
        # bars even on a steep trend (measured: stop lands ~6 bars after trend
        # start). The ER gate is the fast responder; ADX confirms with lag.
        bars = _oscillation_bars(40) + _trend_bars(20)
        gate = CycleGate(ADXGateSignal(period=14))
        stop_at = None
        for i in range(len(bars)):
            d = gate.evaluate(_df(bars[: i + 1]))
            if d.action == "stop" and stop_at is None:
                stop_at = i
        assert stop_at is not None, "trend never stopped the gate"
        assert stop_at <= 40 + 6, f"stop at {stop_at}, expected <= 46"


# ── protocol conformance ────────────────────────────────────────────────────


class TestGateSignalProtocol:
    def test_signals_satisfy_protocol(self):
        for sig in (ERGateSignal(), ADXGateSignal()):
            assert isinstance(sig, GateSignal)
            assert isinstance(sig.name, str) and sig.name

"""Tests for GET /research/lifecycle (factor lifecycle verdict endpoint).

Covers the single-symbol time-series adaptation: per-bar rank-IC split into
epochs, replayed through the calibrated factor_lifecycle state machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.v1.research import (  # noqa: E402
    _factor_turnover,
    _lifecycle_decide,
    _lifecycle_for_factor,
    _long_short_spread,
)


class TestLifecycleDecide:
    def test_healthy_when_stable_above_floor(self) -> None:
        verdict, reason = _lifecycle_decide(
            0.10, 0.10, baseline_ic=0.10, ic_floor=0.02, decay_factor=0.6
        )
        assert verdict == "healthy"
        assert "stable" in reason

    def test_decay_when_below_floor(self) -> None:
        verdict, _ = _lifecycle_decide(
            0.001, 0.001, baseline_ic=None, ic_floor=0.02, decay_factor=0.6
        )
        assert verdict == "decay"

    def test_decay_when_shrunk_vs_baseline(self) -> None:
        # baseline 0.20 * 0.6 = 0.12; recent 0.10 is below but above floor
        verdict, reason = _lifecycle_decide(
            0.10, 0.10, baseline_ic=0.20, ic_floor=0.02, decay_factor=0.6
        )
        assert verdict == "decay"
        assert "baseline" in reason

    def test_nan_ic_is_healthy(self) -> None:
        verdict, _ = _lifecycle_decide(
            float("nan"),
            float("nan"),
            baseline_ic=0.20,
            ic_floor=0.02,
            decay_factor=0.6,
        )
        assert verdict == "healthy"


class TestLifecycleForFactor:
    def _series(self, values: list[float]) -> pd.Series:
        return pd.Series(values, index=pd.RangeIndex(len(values)))

    def test_none_when_data_too_short(self) -> None:
        rng = np.random.default_rng(1)
        f = self._series(list(rng.normal(0, 0.01, 150)))
        r = self._series(list(rng.normal(0, 0.01, 150)))
        assert _lifecycle_for_factor(f, r, epoch_bars=100) is None

    def test_predictive_factor_stays_active(self) -> None:
        rng = np.random.default_rng(7)
        n = 600
        trend = np.cumsum(rng.normal(0.001, 0.01, n))
        f = self._series(list(trend[:-1]))
        r = self._series(list(trend[1:]))
        res = _lifecycle_for_factor(f, r, epoch_bars=100)
        assert res is not None
        assert res["state"] in ("ACTIVE", "WATCH")
        assert res["baseline_ic"] > 0.1
        assert len(res["ic_history"]) > 0
        assert len(res["epochs"]) >= 3

    def test_dead_factor_escalates(self) -> None:
        rng = np.random.default_rng(3)
        n = 800
        f = self._series(list(rng.normal(0, 0.01, n)))
        r = self._series(list(rng.normal(0, 0.01, n)))
        res = _lifecycle_for_factor(f, r, epoch_bars=100)
        assert res is not None
        assert res["state"] in ("WATCH", "DECAYING", "RETIRED")
        # state machine must only escalate stepwise through the epochs
        order = {"ACTIVE": 0, "WATCH": 1, "DECAYING": 2, "RETIRED": 3}
        seq = [order[e["state_after"]] for e in res["epochs"]]
        assert all(b - a in (0, 1) for a, b in zip(seq, seq[1:])), seq

    def test_uses_calibrated_transitions(self) -> None:
        from selene_space_alpha.factor_lifecycle import TRANSITIONS, FactorState

        # RETIRED must be terminal in the kernel the endpoint uses
        assert TRANSITIONS[FactorState.RETIRED]["decay"] == FactorState.RETIRED

    def test_stable_negative_ic_factor_stays_active(self) -> None:
        # Contrarian factor: return = -factor + noise → stable NEGATIVE IC.
        # Sign-orientation must prevent the kernel's positive-IC decay check
        # from flagging it (real-data incident 0922: all indicators RETIRED).
        rng = np.random.default_rng(9)
        n = 800
        f = self._series(list(rng.normal(0, 1, n)))
        r = self._series(list(-0.5 * f.to_numpy() + rng.normal(0, 0.3, n)))
        res = _lifecycle_for_factor(f, r, epoch_bars=100)
        assert res is not None
        assert res["state"] in ("ACTIVE", "WATCH")
        assert res["baseline_ic"] > 0.1  # oriented to working direction


class TestHelpers:
    def test_long_short_spread_sign(self) -> None:
        rng = np.random.default_rng(5)
        n = 500
        f = pd.Series(rng.normal(0, 1, n))
        # return tracks factor rank -> positive spread
        r = f + rng.normal(0, 0.1, n)
        spread = _long_short_spread(f, r)
        assert not np.isnan(spread)
        assert spread > 0

    def test_long_short_spread_nan_on_short_data(self) -> None:
        f = pd.Series([1.0, 2.0, 3.0])
        r = pd.Series([0.1, 0.2, 0.3])
        assert np.isnan(_long_short_spread(f, r))

    def test_turnover_in_range(self) -> None:
        rng = np.random.default_rng(11)
        f = pd.Series(rng.normal(0, 1, 400))
        t = _factor_turnover(f)
        assert not np.isnan(t)
        assert 0.0 <= t <= 1.0

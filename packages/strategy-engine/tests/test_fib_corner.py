"""Tests for the Fibonacci-grid corner-state module (fib_corner).

Covers the two causality corrections the promotion was made to enforce:
- the zigzag advances on the *confirmation* bar, never the price extreme;
- the corner filter only reads a prior test that has already resolved.

Plus the execution simulator's barrier race (target vs stop) and the
single-position replay runner's order discipline.
"""

from __future__ import annotations

import numpy as np
import pytest

from selene_space_strategy.fib_corner import (
    CornerExecutionStrategy,
    annotate_prior,
    build_events,
    causal_zigzag,
    corner_filter,
    market_entry_bars,
    normalise_participation,
    participation_cut,
    simulate_arm,
    train_cuts,
    vstate_cut,
)
from selene_space_backtest.broker import SimulatedBroker
from selene_space_backtest.commissions import NoCommission
from selene_space_types.market import KlineEvent
from selene_space_types.trading import Order, OrderSide, OrderType


def _walk(n: int, seed: int = 0, drift: float = 0.0, vol: float = 0.002):
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, vol, size=n)
    close = 100.0 * np.exp(np.cumsum(steps))
    high = close * (1 + np.abs(rng.normal(0, vol / 2, size=n)))
    low = close * (1 - np.abs(rng.normal(0, vol / 2, size=n)))
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    ts = np.arange(n, dtype=float) * 60_000.0
    vol_ = np.full(n, 10.0)
    taker = vol_ * 0.5
    return ts, open_, high, low, close, vol_, taker


class TestCausalZigzag:
    def test_confirmation_is_never_before_the_extreme(self):
        ts, o, h, l, c, v, tb = _walk(500, seed=1)
        pivots = causal_zigzag(h, l, 0.01)
        assert pivots, "expected pivots on a random walk with 1% threshold"
        for pivot_idx, _px, _kind, confirm_idx in pivots:
            assert confirm_idx >= pivot_idx

    def test_pivot_kind_alternates(self):
        ts, o, h, l, c, v, tb = _walk(800, seed=2)
        kinds = [k for _i, _p, k, _c in causal_zigzag(h, l, 0.01)]
        for a, b in zip(kinds, kinds[1:]):
            assert a != b

    def test_empty_input(self):
        assert causal_zigzag(np.array([]), np.array([]), 0.01) == []


class TestBuildEventsCausality:
    def test_events_carry_only_past_derived_axes(self):
        ts, o, h, l, c, v, tb = _walk(3000, seed=3, vol=0.004)
        events = build_events(ts, h, l, c, v, tb, threshold=0.01, horizon=60)
        assert events, "expected at least one corner touch"
        for ev in events:
            assert ev["exit_bar"] > ev["i"]
            assert ev["cd"] in (0, 1)
            assert np.isfinite(ev["part"]) and np.isfinite(ev["vstate"])
            assert ev["k"] not in (0, 10)

    def test_prior_requires_resolution(self):
        ts, o, h, l, c, v, tb = _walk(3000, seed=4, vol=0.004)
        events = build_events(ts, h, l, c, v, tb, threshold=0.01, horizon=60)
        annotate_prior(events)
        for ev in events:
            if ev["psame"] is not None:
                assert ev["pgap"] >= 0

    def test_rsi_mode_needs_rsi(self):
        ts, o, h, l, c, v, tb = _walk(500, seed=5)
        with pytest.raises(ValueError):
            build_events(ts, h, l, c, v, tb, touch_mode="rsi", rsi=None)


class TestCornerFilter:
    def test_filter_reads_only_prior_axes(self):
        events = [
            {
                "i": 10,
                "exit_bar": 20,
                "out": 0,
                "part": 1.0,
                "vstate": 1.0,
                "ts": 0,
                "pi": 0,
                "k": 1,
            },
            {
                "i": 30,
                "exit_bar": 40,
                "out": 1,
                "part": 1.0,
                "vstate": 1.0,
                "ts": 100,
                "pi": 0,
                "k": 1,
            },
            {
                "i": 50,
                "exit_bar": 60,
                "out": 0,
                "part": 5.0,
                "vstate": 0.2,
                "ts": 200,
                "pi": 0,
                "k": 1,
            },
        ]
        annotate_prior(events)
        normalise_participation(events, 1.0)
        cuts = {"med_part": 1.0, "part_cut": 1.0, "vstate_cut": 2.0}
        kept = corner_filter(events, cuts)
        # Event 30's prior (bar 10) held (out == 0) and resolved (exit 20 < 30),
        # so 30 survives; event 50's prior (bar 30) did not hold, so 50 drops.
        assert [e["i"] for e in kept] == [30]

    def test_train_cuts_on_empty(self):
        cuts = train_cuts([], [])
        assert cuts["med_part"] == 1.0
        assert cuts["part_cut"] == float("-inf")
        assert cuts["vstate_cut"] == float("inf")

    def test_cut_helpers_ignore_non_finite(self):
        events = [
            {"ppart": float("nan"), "pvstate": float("nan")},
            {"ppart": 2.0, "pvstate": 0.5},
        ]
        assert participation_cut(events) == pytest.approx(2.0)
        assert vstate_cut(events) == pytest.approx(0.5)


class TestSimulateArmBarrierRace:
    """The stop must be able to win the race — no unconditional target win."""

    def _flat_arrays(self, n):
        o = np.full(n, 100.0)
        h = np.full(n, 100.0)
        l = np.full(n, 100.0)
        c = np.full(n, 100.0)
        return o, h, l, c

    def test_stop_wins_when_price_falls_first(self):
        n = 50
        o, h, l, c = self._flat_arrays(n)
        # Entry at bar 6 fills at open 100; gap 4 -> stop 98, target 102.
        for j in range(6, 20):
            c[j] = 90.0
            h[j] = 90.0
            l[j] = 90.0
        ev = {"i": 5, "line": 100.0, "gap": 4.0, "cd": 1, "out": 1}
        trades = simulate_arm(
            [ev], o, h, l, c, entry_mode="market", exit_mode="limit", horizon=40
        )
        assert len(trades) == 1
        assert trades[0]["net_bp"] < 0, "a stop-out must not be booked as a win"

    def test_target_wins_when_price_rises_first(self):
        n = 50
        o, h, l, c = self._flat_arrays(n)
        for j in range(6, 20):
            c[j] = 110.0
            h[j] = 110.0
            l[j] = 110.0
        ev = {"i": 5, "line": 100.0, "gap": 4.0, "cd": 1, "out": 0}
        trades = simulate_arm(
            [ev], o, h, l, c, entry_mode="market", exit_mode="limit", horizon=40
        )
        assert len(trades) == 1
        assert trades[0]["net_bp"] > 0

    def test_ambiguous_bar_resolves_to_stop(self):
        n = 20
        o, h, l, c = self._flat_arrays(n)
        # One bar straddles both barriers -> stop must win.
        h[6] = 120.0
        l[6] = 80.0
        ev = {"i": 5, "line": 100.0, "gap": 4.0, "cd": 1, "out": 0}
        trades = simulate_arm(
            [ev], o, h, l, c, entry_mode="market", exit_mode="limit", horizon=10
        )
        # entry at open of bar 6 = 100; stop hit at bar 6 -> exit at open of bar 7 = 100 -> flat
        assert len(trades) == 1
        assert trades[0]["net_bp"] <= 0

    def test_win_rate_near_half_on_symmetric_walk(self):
        ts, o, h, l, c, v, tb = _walk(4000, seed=7, vol=0.003)
        events = build_events(ts, h, l, c, v, tb, threshold=0.01, horizon=60)
        if len(events) < 30:
            pytest.skip("not enough events on this walk")
        trades = simulate_arm(
            events, o, h, l, c, entry_mode="limit", exit_mode="limit", horizon=60
        )
        win = np.mean([t["net_bp"] > 0 for t in trades])
        assert 0.2 < win < 0.8, f"win rate {win:.2f} suggests a barrier-race bug"


class TestExecutionStrategy:
    def _kline(self, i: int) -> KlineEvent:
        return KlineEvent(
            exchange="backtest",
            symbol="SOLUSDT",
            timestamp=i * 60_000,
            interval="1m",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=10.0,
            closed=True,
        )

    def test_single_position_and_exit(self):
        strat = CornerExecutionStrategy(
            symbol="SOLUSDT",
            interval="1m",
            entries={5: OrderSide.BUY},
            exits={10: True},
            notional=1000.0,
        )
        strat.reset()
        emitted = [strat.on_kline(self._kline(i)) for i in range(20)]
        sides = [s.side for s in emitted if s is not None]
        assert sides == [OrderSide.BUY, OrderSide.SELL]

    def test_no_reentry_while_holding(self):
        strat = CornerExecutionStrategy(
            symbol="SOLUSDT",
            interval="1m",
            entries={5: OrderSide.BUY, 6: OrderSide.SELL},
            exits={10: True},
        )
        strat.reset()
        emitted = [strat.on_kline(self._kline(i)) for i in range(20)]
        sides = [s.side for s in emitted if s is not None]
        assert sides == [OrderSide.BUY, OrderSide.SELL]

    def test_market_entry_bars_map(self):
        trades = [
            {
                "entry_bar": 5,
                "exit_bar": 10,
                "entry_px": 100.0,
                "exit_px": 101.0,
                "gross_bp": 100.0,
                "cost_bp": 10.0,
                "net_bp": 90.0,
                "gap": 1.0,
                "cd": 1,
            },
        ]
        entries, exits = market_entry_bars(trades)
        assert entries == {5: OrderSide.BUY}
        assert exits == {10: True}


class TestLimitFillParityWithEngine:
    """``simulate_arm``'s resting-limit fill must equal the production rule.

    The engine's ``SimulatedBroker._execution_price`` is the authority for
    LIMIT fills (touch test + improvement toward the open). ``simulate_arm``
    restates that rule in a forward loop because the engine has no order
    cancel; these tests feed the same bars to both and require identical
    (bar, price) verdicts, including the no-fill case.
    """

    LINE = 100.0

    # Long: fills when low <= line, at min(line, open).
    LONG_TOUCH = (
        [101.0, 102.0, 101.5, 99.0, 100.0],
        [102.0, 103.0, 102.5, 101.0, 101.0],
        [100.5, 101.0, 100.8, 98.0, 99.0],
    )
    LONG_MISS = (
        [101.0, 102.0, 103.0, 104.0],
        [102.0, 103.0, 104.0, 105.0],
        [100.5, 101.5, 102.5, 103.5],
    )
    # Short: fills when high >= line, at max(line, open).
    SHORT_TOUCH = (
        [99.0, 98.5, 99.2, 101.0, 99.5],
        [99.5, 99.0, 99.8, 102.0, 100.0],
        [98.0, 97.5, 98.2, 100.0, 98.5],
    )
    SHORT_MISS = (
        [99.0, 98.0, 97.0, 96.0],
        [99.5, 98.5, 97.5, 96.5],
        [98.0, 97.0, 96.0, 95.0],
    )

    @staticmethod
    def _bars(opens, highs, lows) -> list[KlineEvent]:
        return [
            KlineEvent(
                exchange="backtest",
                symbol="SOLUSDT",
                timestamp=i * 60_000,
                interval="1m",
                open=o,
                high=h,
                low=lo,
                close=o,
                volume=10.0,
                closed=True,
            )
            for i, (o, h, lo) in enumerate(zip(opens, highs, lows, strict=True))
        ]

    @classmethod
    def _engine_fill(cls, side, line, bars):
        """First bar the production broker is willing to fill at, or None."""
        broker = SimulatedBroker(commission=NoCommission())
        for i, bar in enumerate(bars):
            ticket = Order(
                id=f"probe-{i}",
                symbol="SOLUSDT",
                side=side,
                type=OrderType.LIMIT,
                quantity=1.0,
                price=line,
                created_at=bar.timestamp,
                updated_at=bar.timestamp,
            )
            fill = broker.execute(ticket, bar)
            if fill is not None:
                return i, float(fill.price)
        return None

    @classmethod
    def _arm_fill(cls, cd, line, opens, highs, lows):
        """First bar ``simulate_arm`` fills a limit entry on, or None.

        ``i=-1`` makes the arm's scan start at bar 0, i.e. the same window the
        broker loop above walks from the first bar.
        """
        events = [{"i": -1, "line": line, "gap": 4.0, "cd": int(cd), "out": 0}]
        trades = simulate_arm(
            events,
            np.asarray(opens, float),
            np.asarray(highs, float),
            np.asarray(lows, float),
            np.asarray(opens, float),
            entry_mode="limit",
            exit_mode="market",
            horizon=len(opens),
        )
        if not trades:
            return None
        return trades[0]["entry_bar"], trades[0]["entry_px"]

    @pytest.mark.parametrize(
        ("cd", "arrays"),
        [
            (True, LONG_TOUCH),
            (False, SHORT_TOUCH),
        ],
    )
    def test_fill_bar_and_price_match_engine(self, cd, arrays):
        opens, highs, lows = arrays
        side = OrderSide.BUY if cd else OrderSide.SELL
        engine = self._engine_fill(side, self.LINE, self._bars(opens, highs, lows))
        arm = self._arm_fill(cd, self.LINE, opens, highs, lows)
        assert engine is not None, "fixture must produce a fill"
        assert engine == arm

    @pytest.mark.parametrize(
        ("cd", "arrays"),
        [
            (True, LONG_MISS),
            (False, SHORT_MISS),
        ],
    )
    def test_never_touched_means_no_fill_on_either(self, cd, arrays):
        opens, highs, lows = arrays
        side = OrderSide.BUY if cd else OrderSide.SELL
        assert (
            self._engine_fill(side, self.LINE, self._bars(opens, highs, lows)) is None
        )
        assert self._arm_fill(cd, self.LINE, opens, highs, lows) is None

"""Ported test suite from the user's grid experiment (grid_sim_tests.py @ 43eee46).

TestLevels / TestFills / TestRearm / TestWaterfall are ported with semantics
unchanged (the fixture-parity harness in US-2 depends on this suite staying
green against both engines). TestGateRealDecisions (real decision files) is
intentionally not ported — it depended on QuantDinger-side p0_decision_*.json.
"""

from __future__ import annotations

import pytest

from selene_space_grid import GridSpec
from selene_space_grid.grid_build import compute_levels, seed_initial
from selene_space_grid.grid_engine import GridEngine
from selene_space_grid.grid_fill import rearm, waterfall_exit


def _make_engine(
    lower=90.0, upper=100.0, reference=95.0, count=8, symbol="SOL/USDT"
) -> GridEngine:
    spec = GridSpec(
        symbol=symbol,
        grid_count=count,
        total_budget_quote=100.0,
        lower=lower,
        upper=upper,
        reference=reference,
    )
    engine = GridEngine(spec, {"data": {}})
    engine.compute_levels()
    engine.seed_initial()
    return engine


@pytest.fixture
def engine() -> GridEngine:
    return _make_engine()


# ── level construction ───────────────────────────────────────────────────────


class TestLevels:
    def test_eight_cells_two_legs(self, engine):
        cells = engine.cells
        assert len(cells) == 8
        longs = [c for c in cells if c.side == "long"]
        shorts = [c for c in cells if c.side == "short"]
        assert len(longs) == 4 and len(shorts) == 4

    def test_long_entry_below_exit(self, engine):
        # all cells (long and short) carry a monotonic lower/upper pair;
        # long entry at lower < exit at upper, short entry at upper > exit at lower
        for c in engine.cells:
            assert c.lower < c.upper, f"cell {c.index} bounds inverted"

    def test_seed_cells_exit_ready(self, engine):
        seed_roles = {"long_seed", "short_seed"}
        seeds = [c for c in engine.cells if c.role in seed_roles]
        assert len(seeds) == 2
        for c in seeds:
            assert c.state == "exit_ready", f"seed {c.role} should hold position"

    def test_odd_count_normalized_even(self):
        engine = _make_engine(count=9)
        assert len(engine.cells) == 10

    def test_geometric_mode(self):
        spec = GridSpec(
            symbol="X",
            grid_count=8,
            total_budget_quote=100.0,
            lower=90.0,
            upper=100.0,
            reference=95.0,
            grid_mode="geometric",
        )
        cells = compute_levels(spec)
        # long cells are laid out nearest-reference-first (descending lowers);
        # geometric mode must keep levels strictly monotonic and unique
        longs = [c.lower for c in cells if c.side == "long"]
        assert longs == sorted(
            longs, reverse=True
        ), "long lowers must be strictly descending"
        assert len(set(longs)) == len(longs), "geometric levels must be unique"
        shorts = [c.upper for c in cells if c.side == "short"]
        assert shorts == sorted(shorts), "short uppers must be strictly ascending"
        assert len(set(shorts)) == len(shorts)


# ── fill cycle ───────────────────────────────────────────────────────────────


class TestFills:
    def test_dip_entry_then_exit_completes_cycle(self, engine):
        engine.cells[2].state = "entry_ready"
        dip = {"high": 92.5, "low": 90.5, "close": 91.0, "time": 1, "closed": True}
        ev1 = engine.check_fills([dip])
        assert any(
            e.kind == "entry" and e.index == 2 for e in ev1
        ), "long entry not filled on dip"
        engine.rearm()
        rise = {"high": 94.0, "low": 92.0, "close": 93.0, "time": 2, "closed": True}
        ev2 = engine.check_fills([rise])
        assert any(
            e.kind == "exit" and e.index == 2 for e in ev2
        ), "long exit not filled on rise"
        assert engine.cells[2].cycles == 1

    def test_exit_fills_even_after_rearm(self, engine):
        # after entry fill + rearm, cell is exit_pending — exit should still fire
        engine.cells[2].state = "entry_ready"
        engine.check_fills(
            [{"high": 92.5, "low": 90.5, "close": 91.0, "time": 1, "closed": True}]
        )
        engine.rearm()
        assert engine.cells[2].state == "exit_pending"
        rise = {"high": 94.0, "low": 92.0, "close": 93.0, "time": 2, "closed": True}
        ev = engine.check_fills([rise])
        assert any(e.kind == "exit" and e.index == 2 for e in ev)

    def test_forming_bar_skipped(self, engine):
        engine.cells[2].state = "entry_ready"
        forming = {"high": 92.5, "low": 90.5, "close": 91.0, "time": 1, "closed": False}
        ev = engine.check_fills([forming])
        assert ev == [], "forming bar should not fill"

    def test_one_fill_per_cell_per_bar(self, engine):
        # entry+exit within same bar = one full cycle, no double-entry
        engine.cells[2].state = "entry_ready"
        violent = {"high": 94.0, "low": 90.5, "close": 93.0, "time": 1, "closed": True}
        ev = engine.check_fills([violent])
        fills_for_2 = [e for e in ev if e.index == 2]
        assert len(fills_for_2) == 1, "cell should not fill twice on one bar"

    def test_no_duplicate_cell_on_multi_level_bar(self, engine):
        engine.cells[3].state = "entry_ready"
        engine.cells[5].state = "entry_ready"
        violent = {"high": 99.0, "low": 90.5, "close": 96.0, "time": 1, "closed": True}
        ev = engine.check_fills([violent])
        entry_indices = [e.index for e in ev if e.kind == "entry"]
        assert len(entry_indices) == len(
            set(entry_indices)
        ), "duplicate cell fill on one bar"

    def test_short_leg_fills(self, engine):
        # a short cell in entry_ready sells high when bar high crosses cell.upper
        short_cells = [c for c in engine.cells if c.side == "short"]
        c = short_cells[1]
        c.state = "entry_ready"
        bar = {
            "high": c.upper + 0.5,
            "low": c.upper - 0.2,
            "close": c.upper,
            "time": 1,
            "closed": True,
        }
        ev = engine.check_fills([bar])
        assert any(
            e.kind == "entry" and e.index == c.index and e.side == "short" for e in ev
        )


# ── re-arm / order cap ───────────────────────────────────────────────────────


class TestRearm:
    def test_exits_armed_before_entries(self, engine):
        for c in engine.cells:
            if c.state == "exit_ready":
                c.state = "entry_ready"
        engine.rearm()
        # entries capped at max_open_orders=4; exits all armed first
        pending_exits = sum(1 for c in engine.cells if c.state == "exit_pending")
        pending_entries = sum(1 for c in engine.cells if c.state == "entry_pending")
        assert pending_entries <= engine.spec.max_open_orders
        assert pending_exits + pending_entries == sum(
            1 for c in engine.cells if c.state in ("exit_pending", "entry_pending")
        )

    def test_entry_cap_respected(self, engine):
        # 8 entry_ready cells but max_open_orders=4 -> at most 4 become pending
        for c in engine.cells:
            if c.state == "exit_ready":
                c.state = "entry_ready"
        engine.rearm()
        pending = sum(1 for c in engine.cells if c.state == "entry_pending")
        assert pending == engine.spec.max_open_orders


# ── waterfall exit ───────────────────────────────────────────────────────────


class TestWaterfall:
    def test_closes_all_open_legs(self, engine):
        engine.rearm()
        wf = engine.waterfall_exit("bounds_breach", 89.5, 4)
        assert sum(1 for c in engine.cells if c.state != "disabled") == 0
        assert len(wf) > 0

    def test_realized_pnl_reported(self, engine):
        # force an open long with a known entry, exit at profit
        long_cells = [c for c in engine.cells if c.side == "long"]
        c = long_cells[0]
        c.state = "exit_ready"
        c.qty = 1.0
        c.last_entry_price = c.lower
        ev = engine.waterfall_exit("test", c.upper, 4)
        long_exit = [e for e in ev if e.kind == "exit" and e.side == "long"]
        assert long_exit and long_exit[0].realized_pnl > 0


# ── stateless drive helpers (parity with builder functions) ─────────────────


def test_builder_functions_match_engine_drive():
    """compute_levels/seed_initial/rearm/waterfall_exit pure functions must
    produce the same cell layout and transitions as the GridEngine facade."""
    spec = GridSpec(
        symbol="SOL/USDT",
        grid_count=8,
        total_budget_quote=100.0,
        lower=90.0,
        upper=100.0,
        reference=95.0,
    )
    cells = compute_levels(spec)
    assert len(cells) == 8
    seed_initial(spec, cells)
    seeds = [c for c in cells if c.role.endswith("_seed")]
    assert all(c.state == "exit_ready" for c in seeds)
    rearm(cells, spec)
    wf = waterfall_exit(cells, ts=4, symbol=spec.symbol, price=89.5)
    assert all(c.state == "disabled" for c in cells)
    assert len(wf) > 0


def test_snapshot_restore_roundtrip():
    engine = _make_engine()
    snap = engine.snapshot()
    engine2 = _make_engine()
    assert engine2.restore(snap) is True
    assert [c.to_dict() for c in engine2.cells] == [c.to_dict() for c in engine.cells]
    assert engine2.initial_allocated == engine.initial_allocated

"""Neutral two-leg grid engine (intra-bar cell-touch fills, re-arm, waterfall).

Ported from the user's own grid experiment in QuantDinger
(``scripts/grid_sim.py`` @ 43eee46, Apache-2.0 upstream).
This package is AGPL-3.0-only.
Semantics are kept identical to the source so the fixture-parity harness
(US-2) can drive both engines from the same frozen bar fixture.

Layering:
- ``cell``      — GridSpec / Cell (5-state FSM) / FillEvent dataclasses
- ``grid_build`` — compute_levels (neutral two-leg expansion) + seed_initial
- ``grid_fill``  — pure fill/fill-market/waterfall/rearm/check_fills functions
- ``grid_engine`` — thin GridEngine facade (source call shape preserved)
- fees: MakerTakerCommission from selene_space_backtest (single repo dialect)

Usage (offline replay drive, mirrors grid_replay.py:63-88 in the source):
    spec = GridSpec(symbol=..., grid_count=8, total_budget_quote=100.0,
                    lower=90.0, upper=100.0, reference=95.0)
    engine = GridEngine(spec, {"data": {}})
    engine.compute_levels()
    engine.seed_initial()
    engine.rearm()
    for bar in bars:            # each bar needs high/low/close/time[, closed]
        events = engine.check_fills([bar])
        engine.rearm()
"""

from selene_space_grid.cell import (
    DEFAULT_BUDGET_QUOTE,
    DEFAULT_GRID_COUNT,
    DEFAULT_INITIAL_POSITION_PCT,
    DEFAULT_MAX_OPEN_ORDERS,
    DEFAULT_MIN_SPREAD,
    ROLE_LONG,
    ROLE_LONG_SEED,
    ROLE_SHORT,
    ROLE_SHORT_SEED,
    Cell,
    CellState,
    FillEvent,
    GridSpec,
)
from selene_space_grid.cycle_gate import (
    ADXGateSignal,
    CycleGate,
    ERGateSignal,
    GateDecision,
    GateSignal,
)
from selene_space_grid.grid_build import compute_levels, grid_points, seed_initial
from selene_space_grid.grid_engine import GridEngine, LedgerLike
from selene_space_grid.grid_fill import (
    check_fills,
    fill_entry,
    fill_exit,
    fill_exit_market,
    open_leg_value,
    rearm,
    restore,
    snapshot,
    waterfall_exit,
)

__all__ = [
    "DEFAULT_BUDGET_QUOTE",
    "DEFAULT_GRID_COUNT",
    "DEFAULT_INITIAL_POSITION_PCT",
    "DEFAULT_MAX_OPEN_ORDERS",
    "DEFAULT_MIN_SPREAD",
    "ADXGateSignal",
    "Cell",
    "CellState",
    "CycleGate",
    "ERGateSignal",
    "FillEvent",
    "GateDecision",
    "GateSignal",
    "GridEngine",
    "GridSpec",
    "LedgerLike",
    "ROLE_LONG",
    "ROLE_LONG_SEED",
    "ROLE_SHORT",
    "ROLE_SHORT_SEED",
    "check_fills",
    "compute_levels",
    "fill_entry",
    "fill_exit",
    "fill_exit_market",
    "grid_points",
    "open_leg_value",
    "rearm",
    "restore",
    "seed_initial",
    "snapshot",
    "waterfall_exit",
]

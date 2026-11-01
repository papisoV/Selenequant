"""Grid level construction: neutral two-leg cell expansion + initial seeding.

Ported from the user's own grid experiment (``grid_sim.py`` @ 43eee46:
``_grid_points`` / ``_compute_levels`` / ``_seed_initial``), with the engine
methods extracted into a builder module so the engine core stays thin.

Semantics preserved exactly (bit-parity requirement):
- even cell count enforced; leg_count = count // 2
- long leg laid out nearest-reference-first (descending lowers)
- short leg cells are (exit_price, entry_price) pairs from the zip
- ``leg_amount = budget * 0.5 / leg_count``; ``target_qty = leg_amount / entry_price``
- seed cells share ``initial_position_pct`` budget, sized by ``cell.lower``
"""

from __future__ import annotations

from selene_space_grid.cell import (
    ROLE_LONG,
    ROLE_LONG_SEED,
    ROLE_SHORT,
    ROLE_SHORT_SEED,
    Cell,
    GridSpec,
)


def grid_points(
    spec: GridSpec, start: float, end: float, cell_count: int
) -> list[float]:
    point_count = max(1, int(cell_count or 0)) + 1
    if spec.grid_mode == "geometric" and start > 0 and end > 0:
        ratio = (end / start) ** (1.0 / float(point_count - 1))
        return [round(start * (ratio**i), 8) for i in range(point_count)]
    step = (end - start) / float(point_count - 1) if point_count > 1 else 0.0
    return [round(start + step * i, 8) for i in range(point_count)]


def compute_levels(spec: GridSpec) -> list[Cell]:
    """Build neutral two-leg cells around reference."""
    count = spec.grid_count
    if count % 2:
        count += 1  # neutral needs even cell count
    leg_count = count // 2
    low, high = sorted([spec.lower, spec.upper])
    reference = spec.reference or (low + high) / 2.0
    if not low < reference < high:
        reference = (low + high) / 2.0

    long_points = grid_points(spec, low, reference, leg_count)
    short_points = grid_points(spec, reference, high, leg_count)
    leg_amount = spec.total_budget_quote * 0.5 / max(1, leg_count)

    cells: list[Cell] = []
    # long leg: entry at lower (buy low), exit at upper (sell high).
    # Nearest cells first (reversed).
    long_cells = list(zip(long_points[:-1], long_points[1:]))
    for entry, exit_price in reversed(long_cells):
        cells.append(
            Cell(
                len(cells),
                ROLE_LONG_SEED if len(cells) == 0 else ROLE_LONG,
                "long",
                entry,
                exit_price,
            )
        )
    # short leg: entry at upper (sell high), exit at lower (buy low to cover).
    short_cells = list(zip(short_points[:-1], short_points[1:]))
    for exit_price, entry in short_cells:
        cells.append(
            Cell(
                len(cells),
                ROLE_SHORT_SEED if len(cells) == leg_count else ROLE_SHORT,
                "short",
                exit_price,
                entry,
            )
        )
    for cell in cells:
        entry_price = cell.lower if cell.side == "long" else cell.upper
        cell.target_qty = leg_amount / entry_price if entry_price > 0 else 0.0
        cell.qty = cell.target_qty
    return cells


def seed_initial(spec: GridSpec, cells: list[Cell]) -> None:
    """Distribute initial position to seed cells -> they're already holding."""
    seed_roles = {c.role for c in cells if c.role.endswith("_seed")}
    if not seed_roles:
        return
    initial_value = spec.total_budget_quote * spec.initial_position_pct
    seed_cells = [c for c in cells if c.role in seed_roles]
    share = initial_value / max(1, len(seed_cells))
    for cell in seed_cells:
        cell.target_qty = share / cell.lower if cell.lower > 0 else 0.0
        cell.qty = cell.target_qty
        cell.state = "exit_ready" if cell.qty > 1e-12 else "disabled"

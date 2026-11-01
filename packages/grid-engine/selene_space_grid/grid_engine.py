"""GridEngine: thin stateful facade over cell/grid_build/grid_fill pure functions.

Keeps the source's ``GridEngine(spec, ledger)`` call shape so the
fixture-parity harness can drive the original engine and this port through
identical offline-drive code (US-2). The ledger protocol is duck-typed
(mapping with ``data`` dict) — selene replays are stateless by design, the
JSON-persisting GridLedger of the source is intentionally not ported.
"""

from __future__ import annotations

from typing import Any, Protocol

from selene_space_backtest.commissions import MakerTakerCommission

from selene_space_grid.cell import Cell, FillEvent, GridSpec
from selene_space_grid.grid_build import compute_levels, seed_initial
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

DEFAULT_MAKER_RATE = 0.001
DEFAULT_TAKER_RATE = 0.001


class LedgerLike(Protocol):
    """Minimal ledger surface the engine touches (mirrors source usage)."""

    data: dict[str, Any]


class GridEngine:
    def __init__(
        self,
        spec: GridSpec,
        ledger: "LedgerLike | dict[str, dict[str, Any]]",
        commission: MakerTakerCommission | None = None,
        maker_rate: float = DEFAULT_MAKER_RATE,
        taker_rate: float = DEFAULT_TAKER_RATE,
    ) -> None:
        self.spec = spec
        self.ledger = ledger
        self.cells: list[Cell] = []
        self.initial_allocated = False
        if commission is not None:
            self.commission = commission
        else:
            self.commission = MakerTakerCommission(
                maker_rate=maker_rate, taker_rate=taker_rate
            )

    # -- level construction --
    def compute_levels(self) -> list[Cell]:
        self.cells = compute_levels(self.spec)
        return self.cells

    def seed_initial(self) -> None:
        seed_initial(self.spec, self.cells)
        self.initial_allocated = True

    # -- fill detection (intra-bar high/low touch) --
    def check_fills(self, bars: list[dict[str, Any]]) -> list[FillEvent]:
        return check_fills(self.cells, self.spec, bars, commission=self.commission)

    def rearm(self) -> None:
        rearm(self.cells, self.spec)

    def waterfall_exit(self, reason: str, price: float, ts: int) -> list[FillEvent]:
        # reason kept for API parity with the source signature
        del reason
        return waterfall_exit(
            self.cells, ts, self.spec.symbol, price, commission=self.commission
        )

    def open_leg_value(self) -> float:
        return open_leg_value(self.cells)

    def snapshot(self) -> dict[str, Any]:
        return snapshot(self.spec, self.cells, self.initial_allocated)

    def restore(self, snap: dict[str, Any]) -> bool:
        result = restore(self.spec, snap)
        if result is None:
            return False
        cells, initial_allocated = result
        self.cells = cells
        self.initial_allocated = initial_allocated
        return True


__all__ = [
    "GridEngine",
    "LedgerLike",
    "check_fills",
    "fill_entry",
    "fill_exit",
    "fill_exit_market",
    "open_leg_value",
    "rearm",
    "restore",
    "snapshot",
    "waterfall_exit",
]

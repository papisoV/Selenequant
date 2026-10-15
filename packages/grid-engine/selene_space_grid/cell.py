"""Core data types for the neutral two-leg grid engine.

Ported from the user's own grid experiment in QuantDinger
(``scripts/grid_sim.py`` @ 43eee46, Apache-2.0 upstream).
This package is AGPL-3.0-only.
Field semantics are kept identical to the source so the fixture-parity
harness can drive both engines from the same data (US-2).
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass
from typing import Any

DEFAULT_GRID_COUNT = 8
DEFAULT_BUDGET_QUOTE = 100.0
DEFAULT_INITIAL_POSITION_PCT = 0.6
DEFAULT_MAX_OPEN_ORDERS = 4
DEFAULT_MIN_SPREAD = 0.0005


class CellState(str, enum.Enum):
    """Five-state FSM per cell — identical vocabulary to the source."""

    ENTRY_READY = "entry_ready"
    ENTRY_PENDING = "entry_pending"
    EXIT_READY = "exit_ready"
    EXIT_PENDING = "exit_pending"
    DISABLED = "disabled"


ROLE_LONG_SEED = "long_seed"
ROLE_LONG = "long"
ROLE_SHORT_SEED = "short_seed"
ROLE_SHORT = "short"


@dataclass(frozen=True)
class GridSpec:
    symbol: str
    side: str = "neutral"
    grid_count: int = DEFAULT_GRID_COUNT
    total_budget_quote: float = DEFAULT_BUDGET_QUOTE
    initial_position_pct: float = DEFAULT_INITIAL_POSITION_PCT
    lower: float = 0.0
    upper: float = 0.0
    reference: float = 0.0
    grid_mode: str = "arithmetic"
    max_open_orders: int = DEFAULT_MAX_OPEN_ORDERS
    min_spread: float = DEFAULT_MIN_SPREAD

    def fingerprint(self) -> str:
        """Identity for resume checks — symbol/bounds/mode drift rejects resume."""
        parts = [
            self.symbol,
            self.side,
            str(self.grid_count),
            f"{self.lower:.8f}",
            f"{self.upper:.8f}",
            self.grid_mode,
        ]
        return "|".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GridSpec":
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in fields})


@dataclass
class Cell:
    index: int
    role: str  # long_seed | long | short_seed | short
    side: str  # long | short
    lower: float
    upper: float
    state: CellState | str = CellState.ENTRY_READY
    cycles: int = 0
    qty: float = 0.0
    # qty each re-entry should carry (persists across exit — prevents
    # position self-decay across cycles; source: grid_sim.py Cell.target_qty)
    target_qty: float = 0.0
    last_entry_price: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = (
            str(self.state.value) if isinstance(self.state, CellState) else self.state
        )
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Cell":
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in fields})


@dataclass
class FillEvent:
    ts: int
    symbol: str
    index: int
    side: str  # long | short
    kind: str  # entry | exit
    price: float
    qty: float
    value: float  # qty * price
    realized_pnl: float = 0.0
    fee: float = (
        0.0  # quote-currency commission (MakerTakerCommission); gross PnL above
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

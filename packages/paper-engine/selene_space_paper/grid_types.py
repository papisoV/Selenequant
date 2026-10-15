"""Pydantic models for the grid execution engine's API layer.

These types mirror the state shape returned by a Node.js grid bot's
getState() method, providing typed contracts for the paper-engine grid
implementation and its HTTP API consumers.
"""

from __future__ import annotations

from pydantic import BaseModel

# Re-export OutOfRangeAction from dex.types for convenience;
# the paper-engine __init__.py imports it from here.
from selene_space_exchange.dex.types import OutOfRangeAction  # noqa: F401


class GridConfig(BaseModel):
    """Immutable grid configuration set at start time."""

    market_id: int
    display_name: str
    mode: str  # neutral | long | short
    lower: float
    upper: float
    grid_count: int
    size_base: float
    leverage: int
    out_of_range_action: str  # close | recover
    step_size: float | None = None


class GridHealth(BaseModel):
    """Per-exchange health classification surfaced to the dashboard."""

    status: str  # idle | ok | warn | error
    reason: str
    data_source: str
    last_ok_age_ms: float
    price_stale: bool
    place_fails: int
    exchange_open_orders: int | None = None


class GridStats(BaseModel):
    """Cumulative trading statistics, reset via resetStats()."""

    buys: int = 0
    sells: int = 0
    completed_rungs: int = 0
    grid_profit: float = 0.0
    volume: float = 0.0


class GridState(BaseModel):
    """Full grid state snapshot returned by getState()."""

    running: bool
    recovery: bool = False
    config: GridConfig | None = None
    last_price: float | None = None
    out_of_range: bool = False
    balance: float | None = None
    equity: float | None = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    return_pct: float = 0.0
    volume: float = 0.0
    open_orders: int = 0
    exchange_open_orders: int | None = None
    completed_rungs: int = 0
    health: GridHealth | None = None
    stats: GridStats = GridStats()


class RecoveryConfig(BaseModel):
    """Configuration for standalone reduce-only recovery ladders."""

    above_entry_only: bool = False


class RangeAdjustment(BaseModel):
    """New price bounds for an in-flight grid range adjustment."""

    lower: float
    upper: float

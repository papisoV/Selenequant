"""Phase 4 aggregator types — DashboardSnapshot + signal aggregations.

These models back the new endpoints added in Phase 4:
- /api/v1/dashboard (aggregated snapshot)
- /api/v1/intelligence/whale-flow (news + onchain whale merge)
- /api/v1/onchain/smart-money (whales + classification)
- /api/v1/options/vol-surface (IV surface from instruments)
- /api/v1/trading/live-log (SSE stream events)
"""

from __future__ import annotations


from pydantic import BaseModel, ConfigDict, Field

from selene_space_types.common import Timestamp


# ---------------------------------------------------------------------------
# Dashboard snapshot (aggregator output)
# ---------------------------------------------------------------------------


class DashboardMarketSummary(BaseModel):
    """Top-of-book market snapshot for a single symbol."""

    symbol: str = Field(..., description="Normalized pair, e.g. BTC/USDT")
    last_price: float | None = Field(None, description="Most recent trade price")
    change_24h_pct: float | None = Field(None, description="24h percent change")
    volume_24h: float | None = Field(None, description="24h base volume")

    model_config = ConfigDict(extra="allow")


class DashboardPaperSummary(BaseModel):
    """Paper trading account summary."""

    account_id: str = Field(..., description="Paper account id")
    equity: float | None = Field(None, description="Current account equity (USDT)")
    unrealized_pnl: float | None = Field(
        None, description="Unrealized PnL across open positions"
    )
    open_positions: int | None = Field(None, description="Count of open positions")

    model_config = ConfigDict(extra="allow")


class DashboardTradingSummary(BaseModel):
    """Live trading summary."""

    mode: str = Field(..., description="Execution mode: 'paper' | 'live'")
    open_orders: int | None = Field(None, description="Count of open orders")
    open_positions: int | None = Field(None, description="Count of open positions")
    kill_switch: bool = Field(False, description="Whether kill switch is engaged")

    model_config = ConfigDict(extra="allow")


class DashboardSignalsSummary(BaseModel):
    """Active signal counts by severity."""

    total: int = Field(0, description="Total active signals")
    high_severity: int = Field(0, description="High-severity signals")
    medium_severity: int = Field(0, description="Medium-severity signals")
    low_severity: int = Field(0, description="Low-severity signals")

    model_config = ConfigDict(extra="allow")


class DashboardSnapshot(BaseModel):
    """Aggregated snapshot returned by /api/v1/dashboard.

    Fan-out result from markets + paper + trading + signals with 800ms
    per-sub-call timeout and 5s overall cache.
    """

    ts: Timestamp = Field(..., description="Snapshot generation timestamp (ms)")
    markets: list[DashboardMarketSummary] = Field(default_factory=list)
    paper: DashboardPaperSummary | None = Field(None)
    trading: DashboardTradingSummary | None = Field(None)
    signals: DashboardSignalsSummary | None = Field(None)
    slow_subcalls: list[str] = Field(
        default_factory=list,
        description="Sub-call names that exceeded the 800ms budget and were dropped",
    )

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Whale flow (intelligence)
# ---------------------------------------------------------------------------


class WhaleFlowItem(BaseModel):
    """Single whale-flow event — news/social + onchain whale movement."""

    ts: Timestamp = Field(..., description="Event timestamp (ms)")
    symbol: str = Field(..., description="Asset symbol, e.g. BTC")
    direction: str = Field(..., description="'inflow' | 'outflow' | 'neutral'")
    value_usd: float | None = Field(None, description="Notional value in USD")
    source: str = Field(..., description="'onchain' | 'news' | 'social'")
    headline: str | None = Field(None, description="Headline or movement description")
    severity: str = Field("medium", description="'high' | 'medium' | 'low'")

    model_config = ConfigDict(extra="allow")


class WhaleFlow(BaseModel):
    """Aggregated whale-flow response."""

    symbol: str = Field(..., description="Asset symbol queried")
    items: list[WhaleFlowItem] = Field(default_factory=list)
    count: int = Field(0, description="Item count")

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Smart money (onchain)
# ---------------------------------------------------------------------------


class SmartMoneyMovement(BaseModel):
    """A whale movement enriched with classification tags."""

    ts: Timestamp = Field(..., description="Movement timestamp (ms)")
    symbol: str = Field(..., description="Asset symbol")
    direction: str = Field(..., description="'accumulate' | 'distribute' | 'transfer'")
    value_usd: float | None = Field(None, description="Notional in USD")
    label: str = Field(
        ...,
        description="'smart_money_in' | 'smart_money_out' | 'exchange_outflow' | 'exchange_inflow'",
    )
    confidence: float = Field(0.5, description="Classification confidence 0..1")
    hash: str | None = Field(None, description="Transaction hash")

    model_config = ConfigDict(extra="allow")


class SmartMoney(BaseModel):
    """Aggregated smart-money movements for an asset."""

    symbol: str = Field(..., description="Asset symbol queried")
    movements: list[SmartMoneyMovement] = Field(default_factory=list)
    count: int = Field(0, description="Movement count")

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Vol surface (options)
# ---------------------------------------------------------------------------


class VolSurfacePoint(BaseModel):
    """Single IV point on the surface grid."""

    expiry: str = Field(..., description="Expiry label, e.g. '2026-08-29'")
    strike: float = Field(..., description="Strike price")
    iv: float = Field(..., description="Implied volatility (decimal, e.g. 0.65 = 65%)")
    iv_change: float | None = Field(
        None, description="24h IV change (percentage points)"
    )
    volume: int | None = Field(None, description="24h volume at this strike/expiry")

    model_config = ConfigDict(extra="allow")


class VolSurface(BaseModel):
    """Implied volatility surface for an underlying asset."""

    underlying: str = Field(..., description="Underlying asset symbol, e.g. BTC")
    ts: Timestamp = Field(..., description="Surface generation timestamp (ms)")
    points: list[VolSurfacePoint] = Field(default_factory=list)
    expiries: list[str] = Field(
        default_factory=list, description="Available expiry labels"
    )
    strikes: list[float] = Field(default_factory=list, description="Available strikes")

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Live log (trading)
# ---------------------------------------------------------------------------


class LiveLogEntry(BaseModel):
    """A single fill/order event streamed by /trading/live-log."""

    ts: Timestamp = Field(..., description="Event timestamp (ms)")
    event_type: str = Field(
        ...,
        description="'order_placed' | 'order_filled' | 'order_cancelled' | 'position_closed'",
    )
    symbol: str = Field(..., description="Instrument symbol")
    side: str | None = Field(None, description="'buy' | 'sell'")
    qty: float | None = Field(None, description="Filled/placed quantity")
    price: float | None = Field(None, description="Execution price")
    order_id: str | None = Field(None, description="Order id")
    account_id: str | None = Field(None, description="Owning account id")

    model_config = ConfigDict(extra="allow")

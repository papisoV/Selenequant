"""Types for perpetual DEX adapters (Decibel, Extended, RISEx)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from selene_space_types.trading import OrderSide


class OutOfRangeAction(str, Enum):
    """Action when price exits the grid range."""

    CLOSE = "close"
    RECOVER = "recover"


class GridMode(str, Enum):
    """Grid operating mode."""

    NEUTRAL = "neutral"
    LONG = "long"
    SHORT = "short"


class DEXMarket(BaseModel):
    """Market info from a perpetual DEX."""

    market_id: int = Field(..., description="Internal numeric market ID (per-session)")
    name: str = Field(..., description="Market name e.g. 'BTC-USD'")
    display_name: str = Field("", description="Human-readable display name")
    symbol: str = Field("", description="Base asset symbol e.g. 'BTC'")
    last_price: float = 0.0
    step_size: float = Field(0.0, description="Minimum quantity increment")
    step_price: float = Field(0.0, description="Minimum price increment")
    max_leverage: int = 20
    min_order_size: float = 0.0
    qty_step: str = Field("", description="Quantity step as decimal string")
    price_step: str = Field("", description="Price step as decimal string")


class DEXPosition(BaseModel):
    """Open position on a perpetual DEX."""

    size_base: float = Field(0.0, description="Position size (negative = short)")
    entry_price: float = 0.0
    unrealized_pnl: float = 0.0
    leverage: float | None = None


class DEXFill(BaseModel):
    """Fill event from a perpetual DEX."""

    order_id: str
    market_id: int
    side: OrderSide
    price: float
    size_base: float
    level_index: int = Field(-1, description="Grid level index for grid bots")


class DEXPriceUpdate(BaseModel):
    """Price update event from a perpetual DEX."""

    market_id: int
    price: float


class GridOrderInfo(BaseModel):
    """Tracked grid order with metadata."""

    order_id: str
    market_id: int
    level_index: int
    side: OrderSide
    price: float
    size_base: float
    placed_at: float = Field(0.0, description="Unix ms when placed")
    seen: bool = Field(False, description="Whether confirmed resting on exchange")
    external_id: str = Field("", description="Exchange-external ID for resolution")

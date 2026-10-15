"""Trading data models for SeleneQuant.

Covers order lifecycle, fills, positions, PnL records,
exchange-originated private events, and execution mode.

H19 complete fix: 41 amount/price/quantity fields migrated from float to
Decimal to eliminate DTO-layer precision pollution. Internal math stays
Decimal end-to-end; JSON serialization emits float via a wildcard
``@field_serializer`` so frontend contracts are unchanged. Float/dict/string
inputs are coerced to ``Decimal(str(v))`` at the boundary via a wildcard
``@field_validator(mode="before")`` to avoid float-to-Decimal precision
contamination.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from selene_space_types.common import Symbol, Timestamp


def _to_decimal(v: Any) -> Decimal:
    """Coerce a value to Decimal via str to avoid float precision pollution.

    Used by the wildcard ``@field_validator(mode="before")`` on every Decimal
    field. Pass-through for already-Decimal values; None is handled by the
    caller (Optional fields).
    """
    if isinstance(v, Decimal):
        return v
    if v is None:
        return v  # type: ignore[return-value]
    if isinstance(v, bool):
        return Decimal(int(v))
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(str(v))
    if isinstance(v, str):
        return Decimal(v)
    return Decimal(str(v))


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ExecutionMode(str, Enum):
    """Trading execution mode."""

    PAPER = "paper"
    SHADOW = "shadow"
    CONFIRM = "confirm"
    LIMITED_AUTO = "limited_auto"
    FULL_AUTO = "full_auto"
    LIVE = "live"


class OrderSide(str, Enum):
    """Direction of an order."""

    BUY = "buy"
    SELL = "sell"


class MarginMode(str, Enum):
    """Margin mode for derivatives trading (mirrors OKX tdMode)."""

    CROSS = "cross"
    ISOLATED = "isolated"
    CASH = "cash"


class PositionSide(str, Enum):
    """Position side for dual-position mode (mirrors OKX posSide)."""

    LONG = "long"
    SHORT = "short"
    NET = "net"


class OrderType(str, Enum):
    """Supported order types."""

    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"
    IOC = "ioc"


class OrderStatus(str, Enum):
    """Order lifecycle states.

    Transition: created → accepted → partial → filled
                                   ↘ cancelled
                                   ↘ expired
                                   ↘ pending_cancel → cancelled
                                   ↘ triggered → filled
                        ↘ rejected
    """

    CREATED = "created"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    # Extended states for real exchange semantics (the live-trading design Phase 0.1)
    EXPIRED = "expired"  # Order TTL elapsed (e.g. IOC/FOK unfilled, or GTT expiry)
    PENDING_CANCEL = "pending_cancel"  # Cancel requested, awaiting exchange confirmation
    TRIGGERED = "triggered"  # Stop/conditional order triggered, awaiting fill


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------


class Order(BaseModel):
    """A trading order with full lifecycle tracking.

    Supports both spot (cash) and derivatives (cross/isolated margin) modes,
    mirroring OKX's ``/api/v5/trade/order`` contract.
    """

    id: str = Field(..., description="Unique order identifier")
    symbol: Symbol = Field(..., description="Normalized trading pair, e.g. BTC-USDT-SWAP")
    side: OrderSide = Field(..., description="Buy or sell")
    type: OrderType = Field(..., description="Order type")
    status: OrderStatus = Field(OrderStatus.CREATED, description="Current order status")
    quantity: Decimal = Field(..., gt=0, description="Order size in quote currency (USDT)")
    price: Decimal | None = Field(None, ge=0, description="Limit price (None for market orders)")
    stop_price: Decimal | None = Field(None, ge=0, description="Stop trigger price")
    filled_quantity: Decimal = Field(default=Decimal("0"), ge=0, description="Total quantity filled so far (USDT)")
    avg_fill_price: Decimal | None = Field(None, ge=0, description="Volume-weighted average fill price")
    leverage: int = Field(1, ge=1, le=125, description="Leverage multiplier")
    margin_mode: MarginMode = Field(MarginMode.CROSS, description="Margin mode: cross/isolated/cash")
    pos_side: PositionSide = Field(PositionSide.NET, description="Position side: long/short/net")
    reduce_only: bool = Field(False, description="Close position only")
    client_order_id: str | None = Field(None, description="Client-supplied order ID; used by ShadowExecutionEngine to key OrderJournal entries (mark_confirmed).")
    created_at: Timestamp = Field(..., description="Order creation time (ms epoch)")
    updated_at: Timestamp = Field(..., description="Last update time (ms epoch)")

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "ord-001",
                    "symbol": "BTC-USDT-SWAP",
                    "side": "buy",
                    "type": "limit",
                    "status": "created",
                    "quantity": 100.0,
                    "price": 67000.0,
                    "stop_price": None,
                    "filled_quantity": 0.0,
                    "avg_fill_price": None,
                    "leverage": 10,
                    "margin_mode": "cross",
                    "pos_side": "long",
                    "reduce_only": False,
                    "created_at": 1711000000000,
                    "updated_at": 1711000000000,
                }
            ]
        },
    )

    @field_serializer("*", when_used="json")
    def _serialize_decimal_json(self, v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        return v

    @field_validator("quantity", "price", "stop_price", "filled_quantity", "avg_fill_price", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        return _to_decimal(v)

    @property
    def remaining_quantity(self) -> Decimal:
        """Quantity not yet filled (quantity - filled_quantity)."""
        remaining = self.quantity - self.filled_quantity
        return remaining if remaining > 0 else Decimal("0")


# ---------------------------------------------------------------------------
# Fill
# ---------------------------------------------------------------------------


class Fill(BaseModel):
    """A single fill (execution) against an order.

    Mirrors OKX ``/api/v5/trade/fills-history`` response fields:
    ``fillPx``, ``fillSz``, ``fillPnl``, ``execType``, ``feeRate``,
    ``side``, ``posSide``, ``fee``.
    """

    id: str = Field(..., description="Unique fill identifier")
    order_id: str = Field(..., description="Parent order ID")
    client_order_id: str = Field("", description="Client-assigned order ID for routing fills back to originating deployment (Binance newClientOrderId)")
    price: Decimal = Field(..., gt=0, description="Execution price (OKX fillPx)")
    quantity: Decimal = Field(..., gt=0, description="Executed quantity in USDT notional (OKX fillSz)")
    fee: Decimal = Field(default=Decimal("0"), ge=0, description="Fee charged for this fill (OKX fee)")
    fee_currency: str = Field("USDT", description="Currency of the fee (OKX feeCcy)")
    fee_rate: Decimal = Field(default=Decimal("0"), ge=0, description="Fee rate applied (OKX feeRate)")
    realized_pnl: Decimal = Field(default=Decimal("0"), description="Realized PnL from this fill (OKX fillPnl)")
    exec_type: str = Field("taker", description="Liquidity type: maker or taker (OKX execType)")
    side: str = Field("", description="Order side: buy or sell (OKX side)")
    pos_side: str = Field("", description="Position side: long/short/net (OKX posSide)")
    symbol: str = Field("", description="Instrument ID (OKX instId)")
    leverage: int = Field(1, ge=1, description="Leverage multiplier")
    reduce_only: bool = Field(False, description="Whether this fill closes a position")
    timestamp: Timestamp = Field(..., description="Fill time (ms epoch)")

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "fill-001",
                    "order_id": "ord-001",
                    "price": 67000.0,
                    "quantity": 10000.0,
                    "fee": 5.0,
                    "fee_currency": "USDT",
                    "fee_rate": 0.0005,
                    "realized_pnl": 0.0,
                    "exec_type": "taker",
                    "side": "buy",
                    "pos_side": "long",
                    "symbol": "BTC-USDT-SWAP",
                    "leverage": 10,
                    "reduce_only": False,
                    "timestamp": 1711000001000,
                }
            ]
        },
    )

    @field_serializer("*", when_used="json")
    def _serialize_decimal_json(self, v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        return v

    @field_validator("price", "quantity", "fee", "fee_rate", "realized_pnl", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        return _to_decimal(v)


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------


class Position(BaseModel):
    """An open or closed trading position with derivatives support.

    Mirrors OKX position data: includes leverage, margin, liquidation price,
    and position-side awareness for dual-position mode.
    """

    symbol: Symbol = Field(..., description="Normalized trading pair")
    side: OrderSide = Field(..., description="Position direction (buy=long, sell=short)")
    pos_side: PositionSide = Field(PositionSide.NET, description="Position side for dual-mode")
    quantity: Decimal = Field(..., ge=0, description="Position size in USDT notional")
    quantity_base: Decimal = Field(default=Decimal("0"), ge=0, description="Position size in base currency")
    avg_entry_price: Decimal = Field(..., gt=0, description="Average entry price")
    leverage: int = Field(1, ge=1, le=125, description="Leverage multiplier")
    margin_mode: MarginMode = Field(MarginMode.CROSS, description="Margin mode")
    margin: Decimal = Field(default=Decimal("0"), ge=0, description="Required margin (USDT)")
    liquidation_price: Decimal | None = Field(None, description="Estimated liquidation price")
    unrealized_pnl: Decimal = Field(default=Decimal("0"), description="Unrealized profit/loss")
    unrealized_pnl_pct: Decimal = Field(default=Decimal("0"), description="Unrealized PnL as percentage of margin")
    realized_pnl: Decimal = Field(default=Decimal("0"), description="Realized profit/loss")
    current_price: Decimal | None = Field(None, description="Latest mark price")
    opened_at: Timestamp = Field(..., description="Position open time (ms epoch)")
    updated_at: Timestamp = Field(..., description="Last update time (ms epoch)")
    # Funding / borrow fee tracking. Currently informational only — the
    # Portfolio class tracks these in parallel dicts, but these fields are
    # reserved for the future migration to storing Position objects directly.
    accrued_funding: Decimal = Field(default=Decimal("0"), description="Cumulative funding paid/received on this position")
    last_funding_time: int | None = Field(None, description="ms epoch of last funding charge applied")
    borrow_fee_accrued: Decimal = Field(default=Decimal("0"), description="Cumulative borrow fee paid (short stock positions)")

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={
            "examples": [
                {
                    "symbol": "BTC-USDT-SWAP",
                    "side": "buy",
                    "pos_side": "long",
                    "quantity": 1000.0,
                    "quantity_base": 0.015,
                    "avg_entry_price": 67000.0,
                    "leverage": 10,
                    "margin_mode": "cross",
                    "margin": 100.0,
                    "liquidation_price": 60300.0,
                    "unrealized_pnl": 15.0,
                    "unrealized_pnl_pct": 15.0,
                    "realized_pnl": 0.0,
                    "current_price": 68000.0,
                    "opened_at": 1711000000000,
                    "updated_at": 1711000050000,
                }
            ]
        },
    )

    @field_serializer("*", when_used="json")
    def _serialize_decimal_json(self, v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        return v

    @field_validator(
        "quantity",
        "quantity_base",
        "avg_entry_price",
        "margin",
        "liquidation_price",
        "unrealized_pnl",
        "unrealized_pnl_pct",
        "realized_pnl",
        "current_price",
        "accrued_funding",
        "borrow_fee_accrued",
        mode="before",
    )
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        return _to_decimal(v)


# ---------------------------------------------------------------------------
# PnLRecord
# ---------------------------------------------------------------------------


class PnLRecord(BaseModel):
    """Point-in-time PnL summary for a symbol."""

    symbol: Symbol = Field(..., description="Normalized trading pair")
    realized_pnl: Decimal = Field(default=Decimal("0"), description="Realized profit/loss")
    unrealized_pnl: Decimal = Field(default=Decimal("0"), description="Unrealized profit/loss")
    total_pnl: Decimal = Field(default=Decimal("0"), description="Total PnL (realized + unrealized)")
    fees: Decimal = Field(default=Decimal("0"), ge=0, description="Total fees paid")
    timestamp: Timestamp = Field(..., description="Record time (ms epoch)")

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={
            "examples": [
                {
                    "symbol": "BTC/USDT",
                    "realized_pnl": 200.0,
                    "unrealized_pnl": 150.0,
                    "total_pnl": 350.0,
                    "fees": 5.5,
                    "timestamp": 1711000060000,
                }
            ]
        },
    )

    @field_serializer("*", when_used="json")
    def _serialize_decimal_json(self, v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        return v

    @field_validator("realized_pnl", "unrealized_pnl", "total_pnl", "fees", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        return _to_decimal(v)


# ---------------------------------------------------------------------------
# Exchange-originated private events (WS push)
# ---------------------------------------------------------------------------


class ExchangeOrderUpdate(BaseModel):
    """Real-time order status change pushed from an exchange private WS channel.

    Distinct from ``Order`` which is our internal model — this captures the
    raw exchange event with exchange-native identifiers and status strings.
    """

    exchange: str = Field(..., description="Exchange identifier, e.g. 'binance'")
    exchange_order_id: str = Field(..., description="Exchange-assigned order ID")
    client_order_id: str | None = Field(None, description="Client-assigned order ID")
    symbol: Symbol = Field(..., description="Normalized trading pair")
    side: OrderSide
    order_type: str = Field(..., description="Exchange-native order type string")
    status: str = Field(..., description="Exchange-native status (NEW/PARTIALLY_FILLED/FILLED/CANCELED)")
    quantity: Decimal = Field(..., ge=0)
    filled_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    avg_fill_price: Decimal = Field(default=Decimal("0"), ge=0)
    last_fill_price: Decimal = Field(default=Decimal("0"), ge=0)
    last_fill_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    commission: Decimal = Field(default=Decimal("0"), ge=0)
    commission_asset: str | None = None
    timestamp: Timestamp
    raw: dict[str, Any] | None = Field(None, description="Raw exchange payload for debugging")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer("*", when_used="json")
    def _serialize_decimal_json(self, v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        return v

    @field_validator(
        "quantity",
        "filled_quantity",
        "avg_fill_price",
        "last_fill_price",
        "last_fill_quantity",
        "commission",
        mode="before",
    )
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        return _to_decimal(v)


# ---------------------------------------------------------------------------
# Polymarket user channel events (prediction market, separate from spot/futures)
# ---------------------------------------------------------------------------


class PolymarketOrderEventType(str, Enum):
    """Polymarket user channel order event sub-types.

    Polymarket emits one of these for each order lifecycle transition.
    Distinct from order_type (LIMIT/MARKET) which describes the order
    itself — event_type describes the transition (placement vs update
    vs cancellation).
    """

    PLACEMENT = "PLACEMENT"
    UPDATE = "UPDATE"
    CANCELLATION = "CANCELLATION"


class PolymarketOrderUpdate(BaseModel):
    """Real-time Polymarket order event pushed from the user WS channel.

    Polymarket is a prediction market, not a spot/futures exchange. Orders
    are on outcome tokens (ERC-1155 ``asset_id``) inside a condition
    (``market``), with prices in the 0.01-0.99 range representing
    probability. This is intentionally a separate model from
    :class:`ExchangeOrderUpdate` — forcing Polymarket into the spot/futures
    shape would lose ``asset_id``/``market`` semantics and require
    inventing a fake trading-pair symbol.
    """

    exchange: str = Field("polymarket", description="Exchange identifier")
    order_id: str = Field(..., description="Polymarket order ID")
    asset_id: str = Field(..., description="ERC-1155 outcome token ID")
    market: str = Field(..., description="Condition ID (market identifier)")
    owner: str = Field("", description="Wallet address of order owner")
    side: OrderSide
    price: Decimal = Field(..., ge=0, le=1, description="Probability price 0-1")
    original_size: Decimal = Field(..., ge=0, description="Original order size in shares")
    size_matched: Decimal = Field(default=Decimal("0"), ge=0, description="Cumulative filled shares")
    event_type: PolymarketOrderEventType
    timestamp: Timestamp
    raw: dict[str, Any] | None = Field(None, description="Raw payload for debugging")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer("*", when_used="json")
    def _serialize_decimal_json(self, v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        return v

    @field_validator("price", "original_size", "size_matched", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        return _to_decimal(v)


class PolymarketTradeUpdate(BaseModel):
    """Real-time Polymarket trade (fill) event pushed from the user WS channel."""

    exchange: str = Field("polymarket")
    trade_id: str = Field(..., description="Polymarket trade ID")
    asset_id: str = Field(..., description="ERC-1155 outcome token ID")
    market: str = Field(..., description="Condition ID")
    owner: str = Field("", description="Wallet address")
    side: OrderSide
    price: Decimal = Field(..., ge=0, le=1)
    size: Decimal = Field(..., ge=0, description="Filled shares")
    status: str = Field(
        ...,
        description="Trade lifecycle status: MATCHED/MINED/CONFIRMED/RETRYING/FAILED",
    )
    timestamp: Timestamp
    raw: dict[str, Any] | None = Field(None, description="Raw payload for debugging")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer("*", when_used="json")
    def _serialize_decimal_json(self, v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        return v

    @field_validator("price", "size", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        return _to_decimal(v)


class BalanceUpdate(BaseModel):
    """Account balance snapshot for a single asset, pushed via private WS."""

    exchange: str = Field(..., description="Exchange identifier")
    asset: str = Field(..., description="Asset ticker, e.g. 'BTC'")
    free: Decimal = Field(default=Decimal("0"), ge=0, description="Available balance")
    locked: Decimal = Field(default=Decimal("0"), ge=0, description="Balance locked in open orders")
    timestamp: Timestamp

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer("*", when_used="json")
    def _serialize_decimal_json(self, v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        return v

    @field_validator("free", "locked", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        return _to_decimal(v)


class AccountSnapshot(BaseModel):
    """Full account balance snapshot, typically from REST reconciliation."""

    exchange: str = Field(..., description="Exchange identifier")
    balances: list[BalanceUpdate] = Field(default_factory=list)
    timestamp: Timestamp


class AccountStateSeq(BaseModel):
    """Per-channel event-stream watermark.

    - Trading path: real per-channel counters (orders/positions/balances).
    - Paper path: single per-account counter replicated into all three fields.
    Read synchronously BEFORE getter gather — a conservative lower bound
    ("the snapshot content covers every event with seq <= this watermark").
    """

    orders: int = 0
    positions: int = 0
    balances: int = 0


class AccountState(BaseModel):
    """Serializable full account state document — the resync primitive.

    Built on demand from the execution engine's in-memory state via the
    existing ExecutionEngine getter protocol. Consumable by paper/live/shadow
    (backtest: follow-up adapter) and by agent-runtime REST consumers.
    """

    session_id: str = Field(..., description="Server process identity; changes on restart => clients rebuild from scratch")
    seq: AccountStateSeq = Field(..., description="Lower-bound watermark, read before getter gather")
    exchange: str
    account_id: str
    mode: ExecutionMode = Field(..., description="paper / shadow / live")
    balances: list[BalanceUpdate] = Field(default_factory=list)
    positions: list[Position] = Field(default_factory=list)
    orders: list[Order] = Field(default_factory=list)
    trades: list[Fill] = Field(default_factory=list)
    equity: Decimal | None = Field(None, description="Total equity (wallet + unrealized); None when unavailable (e.g. live without equity tracker)")
    timestamp: Timestamp = Field(..., description="Snapshot taken at (ms epoch)")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer("*", when_used="json")
    def _serialize_decimal_json(self, v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        return v

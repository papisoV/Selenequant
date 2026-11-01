"""Market data event models: tickers, trades, klines, order book L2.

These are the payloads pushed over the real-time data pipeline. Every event
carries ``exchange``, ``symbol``, and millisecond ``timestamp`` so consumers
can route, deduplicate, and order messages uniformly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Market segment a symbol trades on. Plain literal so callers pass raw strings.
MarketType = Literal["spot", "futures"]


class PriceLevel(BaseModel):
    """One price rung in an order book.

    In L2 delta updates a ``quantity`` of ``0.0`` means "remove this level" —
    the value must survive serialization untouched (never coerced or filtered).
    """

    price: float
    quantity: float


class TickerEvent(BaseModel):
    """24h rolling statistics snapshot for one instrument."""

    exchange: str
    symbol: str
    timestamp: int
    last_price: float
    bid: float
    ask: float
    volume_24h: float
    change_24h_pct: float
    # Display name when the venue provides one (A-share quotes carry the
    # Chinese name in field 0). Empty for venues that don't. Additive with a
    # default so every existing constructor stays valid.
    name: str = ""
    # The venue's own previous close for the session — A-share quotes carry the
    # exchange's official previous close in Sina field 2. It is the raw, current
    # basis from which the exchange computes limit prices, so it is preferred
    # over a daily-bar store (for A-shares that store is a stale qfq import).
    # None for venues that publish no such field; consumers fall back to their
    # own daily context. Additive with a default so existing constructors stay
    # valid.
    prev_close: float | None = None


class AnomalyEvent(BaseModel):
    """An intraday price/volume anomaly flagged on an A-share ticker.

    ``kind`` is one of the量价 signal family: ``limit_up`` / ``broken_limit_up``
    (炸板) / ``limit_down`` / ``limit_down_recovery`` (翘板) / ``n_day_high`` /
    ``n_day_low`` / ``volume_surge``. ``prev_close`` is the prior close the
    limit prices were derived from: it prefers the venue's own previous close
    (``TickerEvent.prev_close`` — raw and current, matching the exchange's limit
    basis) and falls back to the daily-bar context when the ticker carries none.
    """

    exchange: str
    symbol: str
    timestamp: int
    kind: Literal[
        "limit_up",
        "broken_limit_up",
        "limit_down",
        "limit_down_recovery",
        "n_day_high",
        "n_day_low",
        "volume_surge",
    ]
    price: float
    prev_close: float
    name: str = ""
    # Every event the market WS bridge forwards carries market_type: it is what
    # broadcast_market_event keys the fan-out channel on. Declared as "intraday"
    # so an A-share anomaly lands on market:astock:intraday:<symbol>, matching
    # the source that produced the ticks, rather than falling back to the
    # bridge's "futures" default. Additive with a default, so existing
    # constructors stay valid.
    market_type: str = "intraday"


class TradeEvent(BaseModel):
    """A single executed trade reported by the exchange."""

    exchange: str
    symbol: str
    timestamp: int
    trade_id: str
    price: float
    quantity: float
    market_type: str = "spot"
    side: str


class KlineEvent(BaseModel):
    """One candlestick bar update from the kline stream."""

    exchange: str
    market_type: str = "spot"
    symbol: str
    timestamp: int
    interval: str
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    closed: bool


class OrderBookL2Snapshot(BaseModel):
    """Full depth-of-book snapshot at a sequence point."""

    exchange: str
    symbol: str
    timestamp: int
    sequence_id: int
    market_type: str = "spot"
    bids: list[PriceLevel] = []
    asks: list[PriceLevel] = []


class OrderBookL2Delta(BaseModel):
    """Incremental depth change since the previous snapshot/delta.

    Lists contain only the touched levels; a level's ``quantity == 0.0``
    signals removal of that price rung.
    """

    exchange: str
    symbol: str
    timestamp: int
    sequence_id: int
    market_type: str = "spot"
    bids: list[PriceLevel] = []
    asks: list[PriceLevel] = []

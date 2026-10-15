"""Derivatives and tactical dashboard event models for SeleneQuant.

Covers: liquidation events, funding rate, open interest, large trade
detection, and large order (whale wall) detection.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from selene_space_types.common import Symbol, Timestamp
from selene_space_types.market import MarketType

# ---------------------------------------------------------------------------
# LiquidationEvent — single forced liquidation
# ---------------------------------------------------------------------------


class LiquidationEvent(BaseModel):
    """A single forced-liquidation order from an exchange."""

    exchange: str = Field(..., description="Exchange identifier")
    symbol: Symbol = Field(..., description="Normalized trading pair")
    market_type: MarketType = Field("futures", description="Always futures for liquidations")
    side: Literal["long", "short"] = Field(..., description="Which side was liquidated: long (SELL) or short (BUY)")
    quantity: float = Field(..., gt=0, description="Liquidation quantity in base currency")
    price: float = Field(..., gt=0, description="Liquidation order price")
    avg_price: float = Field(..., ge=0, description="Average fill price (0 if not filled yet)")
    notional_usd: float = Field(..., ge=0, description="Approximate notional value in USD")
    status: str = Field("FILLED", description="Order status: FILLED, NEW, etc.")
    timestamp: Timestamp = Field(..., description="Event time in millisecond epoch")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "exchange": "binance",
                    "symbol": "BTC/USDT",
                    "side": "long",
                    "quantity": 0.5,
                    "price": 68000.0,
                    "avg_price": 67950.0,
                    "notional_usd": 33975.0,
                    "status": "FILLED",
                    "timestamp": 1711000000000,
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# LiquidationStats — aggregated liquidation statistics per time window
# ---------------------------------------------------------------------------


class LiquidationStats(BaseModel):
    """Aggregated liquidation statistics over a sliding time window."""

    symbol: str = Field("ALL", description="Symbol or 'ALL' for all-market aggregate")
    window: str = Field(..., description="Time window: 15m, 30m, 1h, 4h, 24h")
    long_liquidated_usd: float = Field(0.0, ge=0, description="Total long liquidation value (USD)")
    short_liquidated_usd: float = Field(0.0, ge=0, description="Total short liquidation value (USD)")
    total_liquidated_usd: float = Field(0.0, ge=0, description="Total liquidation value (USD)")
    long_count: int = Field(0, ge=0, description="Number of long liquidation events")
    short_count: int = Field(0, ge=0, description="Number of short liquidation events")
    largest_single_usd: float = Field(0.0, ge=0, description="Largest single liquidation in USD")
    timestamp: Timestamp = Field(..., description="Stats computation time")


# ---------------------------------------------------------------------------
# FundingRateEvent — from markPrice WS or REST polling
# ---------------------------------------------------------------------------


class FundingRateEvent(BaseModel):
    """Funding rate snapshot for a perpetual swap."""

    exchange: str = Field(..., description="Exchange identifier")
    symbol: Symbol = Field(..., description="Normalized trading pair")
    funding_rate: float = Field(..., description="Current funding rate (e.g. 0.0001 = 0.01%)")
    mark_price: float = Field(..., gt=0, description="Current mark price")
    index_price: float = Field(0.0, ge=0, description="Spot index price")
    next_funding_time: Timestamp = Field(0, description="Next funding settlement time (ms epoch)")
    timestamp: Timestamp = Field(..., description="Event time in millisecond epoch")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "exchange": "binance",
                    "symbol": "BTC/USDT",
                    "funding_rate": 0.0001,
                    "mark_price": 68000.0,
                    "index_price": 67990.0,
                    "next_funding_time": 1711036800000,
                    "timestamp": 1711000000000,
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# OpenInterestSnapshot — from REST polling
# ---------------------------------------------------------------------------


class OpenInterestSnapshot(BaseModel):
    """Open interest snapshot for a futures/swap instrument."""

    exchange: str = Field(..., description="Exchange identifier")
    symbol: Symbol = Field(..., description="Normalized trading pair")
    open_interest: float = Field(..., ge=0, description="OI in contracts or base currency")
    open_interest_usd: float = Field(0.0, ge=0, description="OI in USD value")
    timestamp: Timestamp = Field(..., description="Snapshot time in millisecond epoch")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "exchange": "binance",
                    "symbol": "BTC/USDT",
                    "open_interest": 12345.67,
                    "open_interest_usd": 839506560.0,
                    "timestamp": 1711000000000,
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# LargeTradeEvent — trade exceeding a configurable notional threshold
# ---------------------------------------------------------------------------


class LargeTradeEvent(BaseModel):
    """A trade that exceeded the large-trade notional threshold."""

    exchange: str = Field(..., description="Exchange identifier")
    symbol: Symbol = Field(..., description="Normalized trading pair")
    market_type: MarketType = Field("spot", description="Market type: spot or futures")
    side: Literal["buy", "sell"] = Field(..., description="Taker side")
    price: float = Field(..., gt=0, description="Trade price")
    quantity: float = Field(..., gt=0, description="Trade quantity in base currency")
    notional_usd: float = Field(..., gt=0, description="Notional value in USD")
    trade_id: str = Field("", description="Exchange-assigned trade/aggregate ID")
    timestamp: Timestamp = Field(..., description="Trade time in millisecond epoch")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "exchange": "binance",
                    "symbol": "BTC/USDT",
                    "side": "buy",
                    "price": 68000.0,
                    "quantity": 2.5,
                    "notional_usd": 170000.0,
                    "trade_id": "123456789",
                    "timestamp": 1711000000000,
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# LargeOrderEvent — single-level order book entry above threshold
# ---------------------------------------------------------------------------


class LargeOrderEvent(BaseModel):
    """A large resting order detected in the order book."""

    exchange: str = Field(..., description="Exchange identifier")
    symbol: Symbol = Field(..., description="Normalized trading pair")
    market_type: MarketType = Field("spot", description="Market type: spot or futures")
    side: Literal["bid", "ask"] = Field(..., description="Order book side")
    price: float = Field(..., gt=0, description="Price level")
    quantity: float = Field(..., gt=0, description="Quantity at this price level")
    notional_usd: float = Field(..., gt=0, description="Approximate notional value in USD")
    depth_rank: int = Field(0, ge=0, description="Rank in the book (0 = best)")
    event_type: Literal["appeared", "increased", "decreased", "disappeared"] = Field(
        "appeared", description="What happened to this large order"
    )
    timestamp: Timestamp = Field(..., description="Detection time in millisecond epoch")


# ---------------------------------------------------------------------------
# CVDBarEvent — Cumulative Volume Delta bar (OHLC-like for buy-sell imbalance)
# ---------------------------------------------------------------------------


class CVDBarEvent(BaseModel):
    """Cumulative Volume Delta aggregated over a fixed time interval."""

    exchange: str = Field(..., description="Exchange identifier")
    symbol: Symbol = Field(..., description="Normalized trading pair")
    interval: str = Field(..., description="Bar interval: 5m, 1h, 4h")
    buy_volume: float = Field(0.0, ge=0, description="Total buy (taker) volume in base currency")
    sell_volume: float = Field(0.0, ge=0, description="Total sell (taker) volume in base currency")
    buy_notional_usd: float = Field(0.0, ge=0, description="Total buy notional in USD")
    sell_notional_usd: float = Field(0.0, ge=0, description="Total sell notional in USD")
    cvd: float = Field(0.0, description="Cumulative volume delta (buy - sell) in base currency")
    cvd_usd: float = Field(0.0, description="Cumulative volume delta in USD")
    trade_count: int = Field(0, ge=0, description="Number of trades in this bar")
    bar_open_time: Timestamp = Field(..., description="Bar start time in ms epoch")
    bar_close_time: Timestamp = Field(..., description="Bar end time in ms epoch")
    timestamp: Timestamp = Field(..., description="Event emission time in ms epoch")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "exchange": "binance",
                    "symbol": "BTC/USDT",
                    "interval": "5m",
                    "buy_volume": 12.5,
                    "sell_volume": 8.3,
                    "buy_notional_usd": 850000.0,
                    "sell_notional_usd": 564400.0,
                    "cvd": 4.2,
                    "cvd_usd": 285600.0,
                    "trade_count": 342,
                    "bar_open_time": 1711000000000,
                    "bar_close_time": 1711000300000,
                    "timestamp": 1711000300000,
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# LiqMap — Liquidation price clustering map
# ---------------------------------------------------------------------------


class LiqMapCluster(BaseModel):
    """A cluster of liquidation events at a similar price level.

    Unified type supporting both KDE-estimated and event-clustered approaches.
    """

    price: float = Field(..., gt=0, description="Cluster center price")
    side: Literal["long", "short", "neutral"] = Field("neutral", description="Which side dominates")
    # KDE estimation fields
    density: float = Field(0.0, ge=0, description="Relative density / weight (KDE approach)")
    estimated_oi_usd: float = Field(0.0, ge=0, description="Estimated OI at risk in USD (KDE approach)")
    # Event clustering fields
    price_min: float = Field(0.0, gt=0, description="Lowest price in cluster (event approach)")
    price_max: float = Field(0.0, gt=0, description="Highest price in cluster (event approach)")
    long_liquidated_usd: float = Field(0.0, ge=0, description="Total long liquidation USD in cluster")
    short_liquidated_usd: float = Field(0.0, ge=0, description="Total short liquidation USD in cluster")
    total_liquidated_usd: float = Field(0.0, ge=0, description="Total liquidation USD in cluster")
    long_count: int = Field(0, ge=0, description="Number of long liquidation events")
    short_count: int = Field(0, ge=0, description="Number of short liquidation events")
    intensity: float = Field(0.0, ge=0, description="Normalized intensity 0-1 for heatmap coloring")
    cascading_risk: float = Field(0.0, ge=0, le=1, description="Estimated cascading liquidation risk 0-1")
    dominant_side: Literal["long", "short", "neutral"] = Field("neutral", description="Which side dominates")


class LiqMapResult(BaseModel):
    """Liquidation map result for a single leverage tier."""

    exchange: str = Field(..., description="Exchange identifier")
    symbol: Symbol = Field(..., description="Normalized trading pair")
    leverage_type: str = Field(..., description="Leverage tier: all_leverage, high_leverage, low_leverage, optical_opti")
    mark_price: float = Field(..., gt=0, description="Current mark price")
    clusters: list[LiqMapCluster] = Field(default_factory=list, description="Liquidation price clusters")
    long_total_density: float = Field(0.0, ge=0, description="Total long liquidation density")
    short_total_density: float = Field(0.0, ge=0, description="Total short liquidation density")
    timestamp: Timestamp = Field(..., description="Computation time in ms epoch")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "exchange": "binance",
                    "symbol": "BTC/USDT",
                    "leverage_type": "all_leverage",
                    "mark_price": 62700.0,
                    "clusters": [
                        {"price": 62000.0, "density": 0.8, "side": "long", "estimated_oi_usd": 5000000.0},
                        {"price": 63500.0, "density": 0.6, "side": "short", "estimated_oi_usd": 3500000.0},
                    ],
                    "long_total_density": 0.8,
                    "short_total_density": 0.6,
                    "timestamp": 1711000000000,
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# HeatmapMatrix — Z-score normalized liquidation heatmap
# ---------------------------------------------------------------------------


class HeatmapCell(BaseModel):
    """A single cell in the heatmap grid."""

    price_level: float = Field(..., description="Price level (Y axis)")
    time_offset: int = Field(..., description="Time bucket offset from start (X axis)")
    z_score: float = Field(..., description="Z-score normalized density value")
    raw_density: float = Field(..., ge=0, description="Raw density before normalization")


class HeatmapMatrix(BaseModel):
    """Z-score normalized liquidation density heatmap."""

    exchange: str = Field(..., description="Exchange identifier")
    symbol: Symbol = Field(..., description="Normalized trading pair")
    leverage_type: str = Field(..., description="Leverage tier")
    cells: list[HeatmapCell] = Field(default_factory=list, description="Heatmap grid cells")
    price_min: float = Field(..., gt=0, description="Lowest price level in grid")
    price_max: float = Field(..., gt=0, description="Highest price level in grid")
    price_step: float = Field(..., gt=0, description="Price step between rows")
    time_step_ms: int = Field(..., gt=0, description="Time step between columns in ms")
    window_count: int = Field(..., gt=0, description="Number of time columns")
    timestamp: Timestamp = Field(..., description="Computation time in ms epoch")


# ---------------------------------------------------------------------------
# LiqRatio — cross-coin liquidation weight comparison
# ---------------------------------------------------------------------------


class LiqRatioItem(BaseModel):
    """Liquidation ratio for a single symbol."""

    exchange: str = Field(..., description="Exchange identifier")
    symbol: Symbol = Field(..., description="Normalized trading pair")
    leverage_type: str = Field(..., description="Leverage tier used for computation")
    long_liq_weight: float = Field(0.0, ge=0, description="Long liquidation weight (downside risk)")
    short_liq_weight: float = Field(0.0, ge=0, description="Short liquidation weight (upside risk)")
    net_direction: float = Field(0.0, description="Net direction: positive=bullish, negative=bearish")
    mark_price: float = Field(0.0, gt=0, description="Current mark price")
    timestamp: Timestamp = Field(..., description="Computation time in ms epoch")


# ---------------------------------------------------------------------------
# Toxic Orderflow (TOF) — composite toxicity score from CVD + liq + imbalance
# ---------------------------------------------------------------------------


class ToxicOrderflowScore(BaseModel):
    """Toxic orderflow score for a single symbol on a single exchange."""

    exchange: str = Field(..., description="Exchange identifier")
    symbol: Symbol = Field(..., description="Normalized trading pair")
    cvd_delta: float = Field(0.0, description="CVD change over lookback window (negative = selling pressure)")
    liq_imbalance: float = Field(0.0, description="Liquidation imbalance: long_liq - short_liq (positive = longs being liquidated)")
    book_imbalance: float = Field(0.0, ge=-1, le=1, description="Bid/ask volume imbalance: (bid_vol - ask_vol) / (bid_vol + ask_vol)")
    toxicity_score: float = Field(0.0, ge=0, le=100, description="Composite toxicity score 0-100 (higher = more toxic)")
    direction: str = Field("neutral", description="Inferred direction: bullish, bearish, or neutral")
    mark_price: float = Field(0.0, gt=0, description="Current mark price")
    timestamp: Timestamp = Field(..., description="Computation time in ms epoch")


# ---------------------------------------------------------------------------
# Aggregated Orderbook — cross-exchange L2 merge
# ---------------------------------------------------------------------------


class AggregatedOrderbookLevel(BaseModel):
    """A single price level in the aggregated cross-exchange orderbook."""

    price: float = Field(..., gt=0, description="Price level")
    total_quantity: float = Field(..., ge=0, description="Sum of quantity across all exchanges")
    exchange_breakdown: dict[str, float] = Field(
        default_factory=dict,
        description="Quantity per exchange: {exchange: quantity}",
    )


class AggregatedOrderbookSnapshot(BaseModel):
    """Cross-exchange merged L2 orderbook snapshot."""

    symbol: Symbol = Field(..., description="Normalized trading pair")
    bids: list[AggregatedOrderbookLevel] = Field(default_factory=list, description="Bid levels sorted by price descending")
    asks: list[AggregatedOrderbookLevel] = Field(default_factory=list, description="Ask levels sorted by price ascending")
    spread: float = Field(0.0, ge=0, description="Best ask - best bid")
    mid_price: float = Field(0.0, gt=0, description="Mid price from best bid/ask")
    total_bid_value_usd: float = Field(0.0, ge=0, description="Total bid side value in USD")
    total_ask_value_usd: float = Field(0.0, ge=0, description="Total ask side value in USD")
    source_count: int = Field(0, ge=0, description="Number of exchanges contributing")
    timestamp: Timestamp = Field(..., description="Snapshot time in ms epoch")


# ---------------------------------------------------------------------------
# GEX — Gamma Exposure from options chain (Deribit)
# ---------------------------------------------------------------------------


class OptionsStrikeData(BaseModel):
    """Options data for a single strike across calls and puts."""

    strike: float = Field(..., gt=0, description="Strike price")
    expiry_days: float = Field(..., ge=0, description="Days to expiry")
    call_oi: float = Field(0.0, ge=0, description="Call open interest in contracts")
    put_oi: float = Field(0.0, ge=0, description="Put open interest in contracts")
    call_volume: float = Field(0.0, ge=0, description="Call volume in contracts")
    put_volume: float = Field(0.0, ge=0, description="Put volume in contracts")
    call_iv: float = Field(0.0, ge=0, description="Call implied volatility")
    put_iv: float = Field(0.0, ge=0, description="Put implied volatility")
    call_delta: float = Field(0.0, description="Call delta")
    put_delta: float = Field(0.0, description="Put delta")
    call_gamma: float = Field(0.0, description="Call gamma")
    put_gamma: float = Field(0.0, description="Put gamma")


class GEXLevel(BaseModel):
    """A price level where net gamma exposure changes sign or peaks."""

    price: float = Field(..., gt=0, description="Price level")
    net_gex: float = Field(0.0, description="Net gamma exposure at this level (positive = dealer long gamma)")
    label: str = Field("", description="Label: gamma_flip, call_wall, put_wall")


class GEXResult(BaseModel):
    """Gamma exposure analysis result for a symbol."""

    symbol: Symbol = Field(..., description="Underlying asset (e.g. BTC)")
    underlying_price: float = Field(0.0, gt=0, description="Current underlying price")
    total_call_gex: float = Field(0.0, description="Total call gamma exposure (notional USD)")
    total_put_gex: float = Field(0.0, description="Total put gamma exposure (notional USD, negative by convention)")
    net_gex: float = Field(0.0, description="Net GEX: call_gex + put_gex. Positive = dealer long gamma (mean-reversion), Negative = dealer short gamma (momentum)")
    dealer_positioning: str = Field("neutral", description="Dealer positioning: long_gamma, short_gamma, neutral")
    key_levels: list[GEXLevel] = Field(default_factory=list, description="Key gamma levels (flip points, walls)")
    put_call_ratio: float = Field(0.0, ge=0, description="Put/call OI ratio")
    max_pain: float = Field(0.0, gt=0, description="Estimated max pain price")
    strikes: list[OptionsStrikeData] = Field(default_factory=list, description="Per-strike options data")
    timestamp: Timestamp = Field(..., description="Computation time in ms epoch")


# ---------------------------------------------------------------------------
# Composite Signal — multi-factor trading signal
# ---------------------------------------------------------------------------


class SignalFactor(BaseModel):
    """A single factor contributing to a composite signal."""

    name: str = Field(..., description="Factor name: cvd, tof, liqmap, gex, funding, book_imbalance")
    value: float = Field(0.0, description="Raw factor value")
    weight: float = Field(0.0, ge=0, le=1, description="Weight in composite score")
    contribution: float = Field(0.0, description="Weighted contribution to composite score")
    direction: str = Field("neutral", description="Factor direction: bullish, bearish, neutral")


class CompositeSignal(BaseModel):
    """Composite trading signal combining multiple analysis factors."""

    symbol: Symbol = Field(..., description="Trading pair")
    exchange: str = Field(..., description="Exchange identifier")
    side: str = Field("neutral", description="Signal side: buy, sell, neutral")
    strength: float = Field(0.0, ge=0, le=100, description="Signal strength 0-100")
    factors: list[SignalFactor] = Field(default_factory=list, description="Individual factor breakdowns")
    confidence: float = Field(0.0, ge=0, le=1, description="Signal confidence based on factor agreement")
    mark_price: float = Field(0.0, gt=0, description="Current mark price")
    timestamp: Timestamp = Field(..., description="Computation time in ms epoch")


# ---------------------------------------------------------------------------
# KF Index — custom composite index
# ---------------------------------------------------------------------------


class KFIndexConfig(BaseModel):
    """Configuration for a custom KF Index."""

    id: str = Field(..., description="Unique index identifier")
    name: str = Field(..., description="Human-readable index name")
    description: str = Field("", description="Index description")
    factors: dict[str, float] = Field(default_factory=dict, description="Factor name → weight mapping (weights should sum to 1.0)")
    symbols: list[Symbol] = Field(default_factory=lambda: ["BTC/USDT"], description="Symbols to track")
    threshold_bullish: float = Field(60.0, description="Score above this = bullish")
    threshold_bearish: float = Field(40.0, description="Score below this = bearish")
    created_at: Timestamp = Field(0, description="Creation timestamp")


class KFIndexValue(BaseModel):
    """Current computed value of a KF Index."""

    index_id: str = Field(..., description="Index identifier")
    index_name: str = Field(..., description="Index name")
    symbol: Symbol = Field(..., description="Symbol")
    score: float = Field(0.0, ge=0, le=100, description="Composite index score 0-100")
    direction: str = Field("neutral", description="Direction: bullish, bearish, neutral")
    factor_values: dict[str, float] = Field(default_factory=dict, description="Factor name → normalized value")
    factors: list[SignalFactor] = Field(default_factory=list, description="Factor breakdowns")
    timestamp: Timestamp = Field(..., description="Computation time in ms epoch")


# ---------------------------------------------------------------------------
# MergedBook — cross-exchange aggregated orderbook (from selene-master1)
# ---------------------------------------------------------------------------


class MergedPriceLevel(BaseModel):
    """A single price level in a merged (cross-exchange) orderbook."""

    price: float = Field(..., gt=0, description="Price level")
    total_amount: float = Field(..., ge=0, description="Total amount across all exchanges")
    exchange_breakdown: dict[str, float] = Field(
        default_factory=dict,
        description="Amount per exchange, e.g. {'binance': 1.5, 'okx': 0.8}",
    )


class MergedBookSnapshot(BaseModel):
    """Aggregated L2 orderbook across multiple exchanges for the same trading pair."""

    symbol: Symbol = Field(..., description="Normalized trading pair (e.g. BTC/USDT)")
    exchanges: list[str] = Field(..., description="Source exchanges included")
    best_bid: float = Field(0.0, description="Best bid across all exchanges")
    best_ask: float = Field(0.0, description="Best ask across all exchanges")
    spread: float = Field(0.0, ge=0, description="Best ask - best bid")
    mid_price: float = Field(0.0, description="Mid price = (best_bid + best_ask) / 2")
    bids: list[MergedPriceLevel] = Field(default_factory=list, description="Merged bid levels, sorted desc")
    asks: list[MergedPriceLevel] = Field(default_factory=list, description="Merged ask levels, sorted asc")
    bid_depth_usd: float = Field(0.0, ge=0, description="Total bid depth in USD (top N levels)")
    ask_depth_usd: float = Field(0.0, ge=0, description="Total ask depth in USD (top N levels)")
    bid_ask_ratio: float = Field(1.0, description="bid_depth_usd / ask_depth_usd")
    timestamp: Timestamp = Field(..., description="Snapshot time in ms epoch")


# ---------------------------------------------------------------------------
# LiqMap Event Clustering — liquidation heatmap from event stream (from selene-master1)
# ---------------------------------------------------------------------------


class LiqMapZone(BaseModel):
    """A liquidation zone — estimated price range where cascading liquidations may occur."""

    zone_type: Literal["support", "resistance"] = Field(..., description="Support (below price) or resistance (above)")
    price_low: float = Field(..., gt=0, description="Zone lower bound")
    price_high: float = Field(..., gt=0, description="Zone upper bound")
    total_liquidated_usd: float = Field(0.0, ge=0, description="Total liquidated USD in this zone")
    cluster_count: int = Field(0, ge=0, description="Number of clusters in zone")
    cascading_risk: float = Field(0.0, ge=0, le=1, description="Cascading risk for entire zone")
    estimated_magnitude_usd: float = Field(0.0, ge=0, description="Estimated USD at risk if zone breaks")


class LiquidationHeatmapData(BaseModel):
    """Complete liquidation heatmap data for rendering."""

    symbol: Symbol = Field(..., description="Trading pair")
    current_price: float = Field(0.0, ge=0, description="Current mark price (0 if unknown)")
    clusters: list[LiqMapCluster] = Field(default_factory=list, description="Price-level clusters")
    zones: list[LiqMapZone] = Field(default_factory=list, description="Support/resistance zones")
    total_long_liquidated_usd: float = Field(0.0, ge=0, description="Total long liquidated in window")
    total_short_liquidated_usd: float = Field(0.0, ge=0, description="Total short liquidated in window")
    window: str = Field("1h", description="Aggregation window")
    timestamp: Timestamp = Field(..., description="Computation time in ms epoch")


# ---------------------------------------------------------------------------
# Deribit Options — crypto options market data (from selene-master1)
# ---------------------------------------------------------------------------


class OptionsInstrument(BaseModel):
    """Metadata for a Deribit options instrument."""

    instrument_name: str = Field(..., description="e.g. BTC-28MAR25-80000-C")
    base_currency: str = Field(..., description="BTC or ETH")
    expiration_timestamp: Timestamp = Field(..., description="Expiration time ms epoch")
    strike: float = Field(..., gt=0, description="Strike price in USD")
    option_type: Literal["call", "put"] = Field(..., description="Call or Put")
    is_active: bool = Field(True, description="Whether the instrument is currently tradeable")
    tick_size: float = Field(0.0005, description="Minimum tick size")
    contract_size: float = Field(1.0, description="Contract size in BTC/ETH")


class OptionsTicker(BaseModel):
    """Real-time ticker data for a Deribit options instrument."""

    instrument_name: str = Field(..., description="e.g. BTC-28MAR25-80000-C")
    mark_price: float = Field(0.0, ge=0, description="Mark price in base currency (BTC)")
    mark_price_usd: float = Field(0.0, ge=0, description="Mark price in USD")
    underlying_price: float = Field(0.0, gt=0, description="Underlying index price")
    implied_volatility: float = Field(0.0, ge=0, description="Current IV (0-5 range, e.g. 0.65 = 65%)")
    delta: float = Field(0.0, description="Black-Scholes delta")
    gamma: float = Field(0.0, description="Black-Scholes gamma")
    theta: float = Field(0.0, description="Black-Scholes theta")
    vega: float = Field(0.0, description="Black-Scholes vega")
    rho: float = Field(0.0, description="Black-Scholes rho")
    open_interest: float = Field(0.0, ge=0, description="Open interest in contracts")
    volume_24h: float = Field(0.0, ge=0, description="24h volume in contracts")
    bid_price: float = Field(0.0, ge=0, description="Best bid")
    ask_price: float = Field(0.0, ge=0, description="Best ask")
    last_price: float = Field(0.0, ge=0, description="Last traded price")
    timestamp: Timestamp = Field(..., description="Ticker time in ms epoch")


class OptionsSummary(BaseModel):
    """Aggregated options data for a single expiry."""

    base_currency: str = Field(..., description="BTC or ETH")
    expiration_timestamp: Timestamp = Field(..., description="Expiration time")
    days_to_expiry: float = Field(..., ge=0, description="Days until expiry")
    total_call_oi: float = Field(0.0, ge=0, description="Total call open interest")
    total_put_oi: float = Field(0.0, ge=0, description="Total put open interest")
    put_call_ratio: float = Field(1.0, description="Put OI / Call OI")
    max_pain: float = Field(0.0, ge=0, description="Estimated max pain price")
    total_call_volume: float = Field(0.0, ge=0, description="Total call volume 24h")
    total_put_volume: float = Field(0.0, ge=0, description="Total put volume 24h")
    call_iv_atm: float = Field(0.0, ge=0, description="ATM call IV")
    put_iv_atm: float = Field(0.0, ge=0, description="ATM put IV")
    timestamp: Timestamp = Field(..., description="Summary time in ms epoch")


# ---------------------------------------------------------------------------
# Footprint — K-line buy/sell volume distribution per price level (from selene-master1)
# ---------------------------------------------------------------------------


class FootprintLevel(BaseModel):
    """Buy/sell volume at a single price level within a footprint bar."""

    price: float = Field(..., gt=0, description="Price level")
    buy_volume: float = Field(0.0, ge=0, description="Buy (taker) volume at this price")
    sell_volume: float = Field(0.0, ge=0, description="Sell (taker) volume at this price")
    buy_notional_usd: float = Field(0.0, ge=0, description="Buy notional USD")
    sell_notional_usd: float = Field(0.0, ge=0, description="Sell notional USD")
    trade_count: int = Field(0, ge=0, description="Number of trades at this price")


class FootprintBarEvent(BaseModel):
    """Footprint bar: buy/sell volume distribution across price levels within a kline interval."""

    exchange: str = Field(..., description="Exchange identifier")
    symbol: Symbol = Field(..., description="Normalized trading pair")
    interval: str = Field(..., description="Bar interval: 5m, 1h, 4h")
    levels: list[FootprintLevel] = Field(default_factory=list, description="Volume per price level")
    total_buy_volume: float = Field(0.0, ge=0, description="Total buy volume in base currency")
    total_sell_volume: float = Field(0.0, ge=0, description="Total sell volume in base currency")
    total_buy_notional_usd: float = Field(0.0, ge=0, description="Total buy notional USD")
    total_sell_notional_usd: float = Field(0.0, ge=0, description="Total sell notional USD")
    delta: float = Field(0.0, description="buy_volume - sell_volume")
    high_price: float = Field(0.0, ge=0, description="Highest trade price in bar")
    low_price: float = Field(0.0, ge=0, description="Lowest trade price in bar")
    bar_open_time: Timestamp = Field(..., description="Bar start time in ms epoch")
    bar_close_time: Timestamp = Field(..., description="Bar end time in ms epoch")
    timestamp: Timestamp = Field(..., description="Event emission time in ms epoch")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "exchange": "binance",
                    "symbol": "BTC/USDT",
                    "interval": "5m",
                    "levels": [
                        {"price": 68000.0, "buy_volume": 1.5, "sell_volume": 0.8, "trade_count": 42},
                        {"price": 67999.0, "buy_volume": 0.3, "sell_volume": 1.2, "trade_count": 18},
                    ],
                    "total_buy_volume": 1.8,
                    "total_sell_volume": 2.0,
                    "delta": -0.2,
                    "bar_open_time": 1711000000000,
                    "bar_close_time": 1711000300000,
                    "timestamp": 1711000300000,
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# VPVR — Volume Profile Visible Range (from selene-master1)
# ---------------------------------------------------------------------------


class VPVRLevel(BaseModel):
    """Volume at a single price level for volume profile."""

    price: float = Field(..., gt=0, description="Price level (bucket center)")
    total_volume: float = Field(0.0, ge=0, description="Total volume at this price")
    buy_volume: float = Field(0.0, ge=0, description="Buy (taker) volume")
    sell_volume: float = Field(0.0, ge=0, description="Sell (taker) volume")


class VPVRData(BaseModel):
    """Volume Profile Visible Range — volume distribution across price levels."""

    symbol: Symbol = Field(..., description="Normalized trading pair")
    exchange: str = Field("all", description="Source exchange or 'all'")
    levels: list[VPVRLevel] = Field(default_factory=list, description="Volume per price bucket")
    poc_price: float = Field(0.0, description="Point of Control — price with highest volume")
    va_high: float = Field(0.0, description="Value Area high (68% of volume)")
    va_low: float = Field(0.0, description="Value Area low (68% of volume)")
    total_volume: float = Field(0.0, ge=0, description="Total volume in profile")
    total_buy_volume: float = Field(0.0, ge=0, description="Total buy volume")
    total_sell_volume: float = Field(0.0, ge=0, description="Total sell volume")
    bucket_size: float = Field(0.0, gt=0, description="Price bucket size used")
    window: str = Field("1h", description="Lookback window")
    timestamp: Timestamp = Field(..., description="Computation time in ms epoch")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "symbol": "BTC/USDT",
                    "exchange": "binance",
                    "poc_price": 68200.0,
                    "va_high": 68500.0,
                    "va_low": 67800.0,
                    "total_volume": 1250.5,
                    "bucket_size": 50.0,
                    "window": "1h",
                    "timestamp": 1711000000000,
                }
            ]
        }
    )

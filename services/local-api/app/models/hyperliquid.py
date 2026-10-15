"""Pydantic models for Hyperliquid on-chain intelligence."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DataQuality(BaseModel):
    insufficient: bool = False
    leaderboard_only: bool = False
    closed_trades: int = 0
    min_trades: int = 30
    active_days: int = 0
    symbol_count: int = 0


class ScreenedAddressMetrics(BaseModel):
    closed_trades: int = 0
    realized_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    equity_slope: float = 0.0
    curve_stability: int = 0
    positive_trade_ratio: float = 0.0
    negative_trade_ratio: float = 0.0
    volume: float = 0.0
    fees: float = 0.0
    avg_holding_ms: float = 0.0
    symbol_count: int = 0
    max_symbol_pnl_concentration: float = 0.0
    losing_trades: int = 0
    data_quality: DataQuality = Field(default_factory=DataQuality)


class ScreenedAddressDimensions(BaseModel):
    profitability: int = 0
    consistency: int = 0
    risk: int = 0
    sample_quality: int = 0
    copyability: int = 0


class CopySignal(BaseModel):
    address: str = ""
    classification: str = ""
    score: int = 0
    copy_direction: str = "ignore"
    risk_flags: list[str] = Field(default_factory=list)
    data_quality: DataQuality = Field(default_factory=DataQuality)


class LeaderboardInfo(BaseModel):
    account_value: float = 0.0
    window_pnl: float = 0.0
    window_roi: float = 0.0
    window_volume: float = 0.0
    all_time_pnl: float = 0.0
    all_time_roi: float = 0.0
    display_name: str | None = None


class ScreenedAddress(BaseModel):
    address: str
    classification: str = "neutral"
    score: int = 0
    metrics: ScreenedAddressMetrics = Field(default_factory=ScreenedAddressMetrics)
    dimensions: ScreenedAddressDimensions = Field(default_factory=ScreenedAddressDimensions)
    reasons: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    data_quality: DataQuality = Field(default_factory=DataQuality)
    leaderboard: LeaderboardInfo | None = None
    copy_signal: CopySignal | None = None


class EquityCurvePoint(BaseModel):
    time_ms: int = 0
    equity: float = 0.0
    pnl: float = 0.0
    coin: str = "portfolio"


class PositionInfo(BaseModel):
    coin: str = ""
    szi: str = "0"
    entry_px: str | None = None
    position_value: str = "0"
    unrealized_pnl: str = "0"
    leverage: dict = Field(default_factory=dict)


class AddressScore(BaseModel):
    address: str = ""
    classification: str = "neutral"
    score: int = 0
    metrics: ScreenedAddressMetrics = Field(default_factory=ScreenedAddressMetrics)
    dimensions: ScreenedAddressDimensions = Field(default_factory=ScreenedAddressDimensions)
    reasons: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    data_quality: DataQuality = Field(default_factory=DataQuality)
    copy_signal: CopySignal | None = None


class AddressDetails(BaseModel):
    address: str
    window_days: int = 7
    profile_mode: str = "fast"
    score: AddressScore = Field(default_factory=AddressScore)
    equity_curve: list[EquityCurvePoint] = Field(default_factory=list)
    current_positions: list[PositionInfo] = Field(default_factory=list)
    open_orders: list[dict] = Field(default_factory=list)
    closed_trades: list[dict] = Field(default_factory=list)
    historical_orders: list[dict] = Field(default_factory=list)
    portfolio: dict | None = None


class PipelineStatus(BaseModel):
    candidate_source: str = "stats_leaderboard"
    screen_cache_entries: int = 0
    screen_cache_age_s: float = 0.0
    detail_cache_entries: int = 0
    detail_inflight: int = 0
    active_prefetch_jobs: int = 0
    prefetch_jobs: list[dict] = Field(default_factory=list)
    limits: dict = Field(default_factory=dict)


class PrefetchResult(BaseModel):
    job_id: str = ""
    status: str = "queued"
    loaded: int = 0
    target: int = 0
    mode: str = "fast"
    window_days: int = 7
    min_trades: int = 30
    started_at_ms: int = 0
    updated_at_ms: int = 0
    finished_at_ms: int = 0
    error: str | None = None

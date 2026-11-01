"""Strategy domain models: configs, signals, backtest artifacts, deployment.

A :class:`StrategyConfig` is the declarative definition of a trading strategy
(parameters, rules, risk settings, optional multi-timeframe/pairs extensions).
Runs of a config produce :class:`Signal` stream items and, for historical
simulations, a :class:`BacktestResult` with its :class:`BacktestMetrics`.
Versioning and live deployment are tracked by :class:`StrategyVersionSnapshot`
and :class:`StrategyDeployment`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from selene_space_types.common import Timestamp
from selene_space_types.trading import OrderSide


class StrategyType(str, Enum):
    """Strategy archetypes. New templates append new members; values are the
    lowercase identifiers persisted in YAML/storage (``"custom"``, ...)."""

    SMA_CROSS = "sma_cross"
    RSI_REVERSAL = "rsi_reversal"
    MACD = "macd"
    SENTIMENT = "sentiment"
    CUSTOM = "custom"
    ICT = "ict"
    ICT_FVG = "ict_fvg"
    ICT_OB = "ict_ob"
    ICT_SWEEP = "ict_sweep"
    ADX_BREAKOUT = "adx_breakout"
    ADX_RANGE = "adx_range"
    ADX_STOCH_TREND = "adx_stoch_trend"
    ADX_TREND = "adx_trend"
    ATR_BREAKOUT = "atr_breakout"
    ATR_REVERSAL = "atr_reversal"
    ATR_TRAILING = "atr_trailing"
    ATR_VOLATILITY = "atr_volatility"
    BOLLINGER_BREAKOUT = "bollinger_breakout"
    BOLLINGER_REVERSAL = "bollinger_reversal"
    DCA_GRID = "dca_grid"
    EMA_CROSS = "ema_cross"
    FUNDING_RATE_ARB = "funding_rate_arb"
    KDJ_MOMENTUM = "kdj_momentum"
    KDJ_REVERSAL = "kdj_reversal"
    KDJ_TREND = "kdj_trend"
    MA_EXTENDED_RUNS = "ma_extended_runs"
    RSI_MOMENTUM = "rsi_momentum"
    STOCHASTIC_MOMENTUM = "stochastic_momentum"
    STOCHASTIC_REVERSAL = "stochastic_reversal"
    STOCHASTIC_TREND = "stochastic_trend"


class StrategyDirection(str, Enum):
    """Which side(s) of the market a strategy is allowed to trade."""

    LONG_ONLY = "long_only"
    SHORT_ONLY = "short_only"
    NEUTRAL = "neutral"
    BOTH = "both"


class StrategyConfig(BaseModel):
    """Declarative definition of one trading strategy.

    ``timeframes`` / ``secondary_symbols`` are the multi-timeframe and pairs
    trading extensions. Both default to ``None``; empty containers are stored
    as-is (no coercion to None) — callers must treat None and empty alike as
    single-timeframe/no-secondary semantics.
    """

    id: str
    name: str
    type: StrategyType
    description: str = ""
    symbols: list[str]
    interval: str
    direction: StrategyDirection = StrategyDirection.LONG_ONLY
    parameters: dict = {}
    entry_rules: dict = {}
    exit_rules: dict = {}
    risk_params: dict = {}
    timeframes: dict[str, str] | None = None
    secondary_symbols: list[str] | None = None
    tags: list[str] = Field(default_factory=list)
    source: str = "user"
    version: int = 1
    lifecycle_state: str = "draft"


class Signal(BaseModel):
    """A trade signal emitted by a strategy at one point in time.

    ``features`` is an optional point-in-time snapshot of decision-time
    context (indicator states, regime markers) captured by the strategy the
    moment the entry decision was made. It rides along on entry signals so
    completed trades can later be attributed to market state without
    replaying data — exit/closing signals carry ``None``.
    """

    strategy_id: str
    symbol: str
    side: OrderSide
    strength: float
    timestamp: int
    reason: str
    quantity: float | None = Field(None, ge=0.0)
    features: dict | None = Field(
        None, description="Point-in-time decision-context snapshot; entry signals only"
    )


class BacktestMetrics(BaseModel):
    """Performance statistics summarizing one backtest run."""

    total_return: float
    annual_return: float
    # True 时 annual_return 是被 _ANNUAL_CAP(1e4)截断的值,而非真实年化:
    # 短窗口爆仓样本会产出 inf 并炸 JSON 序列化,故封顶。UI 应提示用户,
    # 否则呈现的是一个被静默改过的数字。
    annual_return_capped: bool = False
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    had_no_losses: bool = False
    total_trades: int
    # Extended metrics
    calmar_ratio: float = 0.0
    sortino_ratio: float = 0.0
    expectancy: float = 0.0
    recovery_factor: float = 0.0
    # Overfitting detection
    dsr: float = 0.0
    pbo: float = 0.0
    haircut_sharpe: float = 0.0
    overfit_passed: bool = True
    # Cross-sectional metrics
    ic_mean: float = 0.0
    icir: float = 0.0
    ic_hit_rate: float = 0.0
    # Funding / borrow fee tracking (default 0 → pre-funding behavior unchanged)
    funding_paid: float = 0.0
    borrow_fee_paid: float = 0.0


class BacktestResult(BaseModel):
    """Outcome of one historical simulation of a strategy."""

    id: str
    strategy_id: str
    strategy_version: int = 1
    symbol: str = ""
    interval: str = "1h"
    start_date: datetime
    end_date: datetime
    metrics: BacktestMetrics
    equity_curve: list[float]
    drawdown_curve: list[float] = Field(default_factory=list)
    buy_hold_curve: list[float] = Field(default_factory=list)
    exposure_curve: list[float] = Field(default_factory=list)
    trades: list[dict] = Field(default_factory=list)
    trades_count: int
    created_at: Timestamp
    lookahead_warnings: list[str] = Field(default_factory=list)
    # Resampling robustness (permutation p-value, bootstrap Sharpe CI, equity
    # bands). Empty dict means "not computed" — an older row or a run with the
    # check switched off, which is why it defaults rather than being required.
    robustness: dict = Field(default_factory=dict)
    config_snapshot: dict = Field(default_factory=dict)


class StrategyVersionSnapshot(BaseModel):
    """Immutable capture of a strategy config at one version.

    Imported directly from ``selene_space_types.strategy`` by consumers;
    not re-exported from the package root.
    """

    id: str
    strategy_id: str
    version: int
    config_snapshot: dict = {}
    note: str = ""
    created_at: Timestamp


class StrategyDeployment(BaseModel):
    """Binding of a specific strategy version to a trading account.

    ``model_dump(mode="json")`` output is directly embeddable in API response
    payloads.
    """

    id: str
    strategy_id: str
    strategy_version: int
    account_id: str
    mode: str
    status: str
    created_at: Timestamp

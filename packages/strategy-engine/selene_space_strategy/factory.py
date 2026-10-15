"""Template catalog and signal-builder surface for the strategy engine.

Everything the engine knows about its pre-built strategy lineup lives here:

* :data:`STRATEGY_TEMPLATES` -- the template descriptor catalog, assembled at
  import time from a compact row table (:class:`_CatalogRow`) so that adding a
  template means adding one table row.
* :class:`StrategyFactory` -- turns template ids into configs, compiles them
  into runtimes, and offers :meth:`StrategyFactory.build_strategy_signals` as
  the one-call dispatcher that accepts an id, a config, or a live runtime.
* Vectorized ternary research generators producing ``1`` (buy), ``0`` (sell),
  ``-1`` (hold) frames.
* Multi-indicator fusion recipes (:data:`FUSION_STRATEGIES`) plus their
  vectorized builders behind :func:`build_fusion_strategy_signals`.
* The Telegram news-spike bridge (:func:`build_tg_spike_signals`,
  :func:`build_tg_spike_portfolio_signals`). Its core invariant is strict
  mutual exclusion between the legacy spike source and the event-overlay
  source: at most one of the two may ever be instantiated per session,
  otherwise de-risking would apply twice.

Example::

    from selene_space_strategy.factory import StrategyFactory

    runtime = StrategyFactory.compile("ema_cross")
    emitted = StrategyFactory.build_strategy_signals(runtime, ohlcv_frame)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, NamedTuple, Protocol

import numpy as np
import pandas as pd

from selene_space_strategy.compiler import compile as compile_config
from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.runtime import StrategyRuntime
from selene_space_strategy.templates.presets.statistical.pairs_trading import PairsTradingStrategy
from selene_space_strategy.templates.presets.intraday_seasonality import IntradaySeasonalityStrategy
from selene_space_strategy.templates.presets.rebalance_premium import RebalancePremiumStrategy
from selene_space_strategy.templates.presets.tsmom_vol_target import TsmomVolTargetStrategy
from selene_space_strategy.templates.presets.cross_sectional_momentum import CrossSectionalMomentumStrategy
from selene_space_strategy.templates.presets.january_barometer import JanuaryBarometerStrategy
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


# ---------------------------------------------------------------------------
# Category axis
# ---------------------------------------------------------------------------


class StrategyCategory(str, Enum):
    """Coarse buckets used to organize the template catalog."""

    TREND = "trend"  # rides established direction
    MOMENTUM = "momentum"  # speed-of-move plays
    REVERSION = "reversion"  # fade back toward the mean
    BREAKOUT = "breakout"  # trade range escapes
    VOLATILITY = "volatility"  # vol-regime / carry structures
    TIMING = "timing"  # calendar and session windows
    AI = "ai"  # LLM-emitted verdicts


# ---------------------------------------------------------------------------
# Descriptor object
# ---------------------------------------------------------------------------


@dataclass
class StrategyMetadata:
    """Describes one entry of the pre-built template catalog."""

    id: str
    name: str
    category: StrategyCategory
    description: str
    # Localized display strings (optional).
    name_zh: str = ""
    description_zh: str = ""
    symbols: list[str] = field(default_factory=lambda: ["BTC/USDT"])
    interval: str = "1h"
    default_params: dict[str, Any] = field(default_factory=dict)
    risk_params: dict[str, Any] = field(default_factory=lambda: {
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.06,
        "max_position_pct": 0.1,
        "max_open_positions": 1,
    })
    templates_dir: str = "templates"
    # False for a catalog row that is listed but has no implementation in any
    # resolution path, so a run would silently complete with zero trades. The
    # UI must not offer to run what cannot run; the set of such rows lives in
    # ``_UNIMPLEMENTED_KEYS``.
    runnable: bool = True


class StrategySignalBuilder(Protocol):
    """Anything that can turn history plus a config into signals."""

    def build(self, config: EngineStrategyConfig, data: pd.DataFrame) -> list[Signal]:
        """Emit trading signals for the supplied historical klines."""
        ...


# ---------------------------------------------------------------------------
# Catalog construction
# ---------------------------------------------------------------------------
# Rows hold display strings and business constants only; every descriptor is
# expanded from its row below and parameter/risk books are copied per entry so
# callers mutating one template never leak into another.


def _template_id(key: str) -> str:
    """Derive the canonical ``template-*`` id from a catalog key."""
    return "template-" + key.replace("stochastic_", "stoch-").replace("_", "-")


def _standard_risk_book() -> dict[str, Any]:
    """Fresh copy of the house-default risk book."""
    return {
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.06,
        "max_position_pct": 0.1,
        "max_open_positions": 1,
    }


class _CatalogRow(NamedTuple):
    """One catalog entry before expansion into :class:`StrategyMetadata`."""

    title: str
    title_zh: str
    bucket: StrategyCategory
    blurb: str
    blurb_zh: str
    params: dict[str, Any] = {}
    universe: tuple[str, ...] = ()
    bar_size: str = "1h"
    risk_book: dict[str, Any] | None = None


_CATALOG: dict[str, _CatalogRow] = {
    # ------------------------- trend following --------------------------
    "sma_cross": _CatalogRow(
        "SMA Crossover", "SMA均线交叉", StrategyCategory.TREND,
        "Go long on the fast SMA crossing above the slow SMA, exit on the reverse cross.",
        "快线金叉慢线做多，死叉离场的经典双均线系统。",
        {"sma_short": 20, "sma_long": 50},
    ),
    "ema_cross": _CatalogRow(
        "EMA Crossover", "EMA均线交叉", StrategyCategory.TREND,
        "Dual EMA cross whose exponential weighting answers faster than the SMA twin.",
        "双指数均线交叉，权重偏重新价，反应比 SMA 版本更快。",
        {"ema_fast": 12, "ema_slow": 26},
    ),
    "adx_trend": _CatalogRow(
        "ADX Trend Follower", "ADX趋势跟踪", StrategyCategory.TREND,
        "Only join moves once ADX certifies directional strength above the gate.",
        "以 ADX 强度确认趋势成立后才顺势跟进的门控策略。",
        {"adx_period": 14, "adx_threshold": 25},
    ),
    "atr_trailing": _CatalogRow(
        "ATR Trailing Stop", "ATR移动止损", StrategyCategory.TREND,
        "Chase price with a stop parked an ATR multiple behind, letting trends breathe.",
        "止损以 ATR 倍数跟在价格后方移动，给趋势留足波动余地。",
        {"atr_period": 14, "atr_multiplier": 3.0},
    ),
    "kdj_trend": _CatalogRow(
        "KDJ Trend Following", "KDJ趋势跟踪", StrategyCategory.TREND,
        "Trend riding driven by KDJ readings with tunable smoothing depths.",
        "由 KDJ 读数驱动的趋势跟随版，平滑深度可调。",
        {"kdj_n": 9, "kdj_m1": 3, "kdj_m2": 3},
    ),
    "bb_atr_trend": _CatalogRow(
        "BB ATR Trend", "布林ATR趋势策略", StrategyCategory.TREND,
        "Enter on band expansion plus rising MA plus safe RSI plus rising ATR; unwind on squeeze or MA flip.",
        "开口扩张、均线上行、RSI 未过热且 ATR 走高时入场；缩口或均线转向离场。",
        {"bb_len": 20, "bb_mult": 2.0, "bb_src": "close",
         "atr_len": 14, "rsi_len": 14, "rsi_src": "close",
         "rsi_long_min": 30.0, "rsi_long_max": 70.0,
         "rsi_short_min": 30.0, "rsi_short_max": 70.0,
         "tp_pct": 0.05, "sl_pct": 0.03,
         "enable_long": True, "enable_short": False},
    ),
    "dmi_delta": _CatalogRow(
        "DMI Delta", "DMI差值策略", StrategyCategory.TREND,
        "Smoothed +DI minus -DI delta; its sign and slope pick the side.",
        "把 +DI 与 -DI 的差值平滑后，按符号与斜率决定多空方向。",
        {"dmi_len": 14, "ma_len": 14, "ma_type": "ema",
         "tp_pct": 0.05, "sl_pct": 0.03,
         "enable_long": True, "enable_short": False},
    ),
    "stoch_supertrend": _CatalogRow(
        "STOCH Supertrend", "随机超级趋势策略", StrategyCategory.TREND,
        "Supertrend rails built from a CMO-smoothed Stochastic K; aimed at minute bars.",
        "用 CMO 平滑后的随机指标 K 值铺设超级趋势轨道，面向分钟级 K 线。",
        {"k_len": 14, "k_smooth_len": 9, "length": 2,
         "percent": 2.0, "src_offset": 1000.0,
         "tp_pct": 0.05, "sl_pct": 0.03,
         "enable_long": True, "enable_short": False},
    ),
    "vw_ema_cross": _CatalogRow(
        "VW-EMA Crossover", "量加权EMA交叉策略", StrategyCategory.TREND,
        "Volume-weighted price smoothed by an EMA to catch institution-driven turns.",
        "成交量加权价经 EMA 平滑后再交叉，追踪主力资金推动的趋势变化。",
        {"vw_period": 20, "ema_len": 9, "price_src": "close",
         "tp_pct": 0.05, "sl_pct": 0.03,
         "enable_long": True, "enable_short": False},
    ),
    "ema_triple_cross": _CatalogRow(
        "EMA Triple Cross", "三均线交叉策略", StrategyCategory.TREND,
        "Gold cross admitted only when the triple-EMA stack, normalized momentum and structure filters agree; death cross or ATR stop unwinds.",
        "三均线金叉需同时满足大趋势向上、动量确认与结构过滤；死叉或 ATR 触发止损离场。",
        {"short_period": 10, "mid_period": 30, "long_period": 200,
         "atr_period": 14, "std_period": 20, "threshold_coef": 1.5,
         "stop_atr_mult": 2.0, "price_src": "typical",
         "tp_pct": 0.05, "sl_pct": 0.02,
         "enable_long": True, "enable_short": False},
    ),
    "pyramid_ma": _CatalogRow(
        "Pyramid MA", "金字塔均线策略", StrategyCategory.TREND,
        "While the four-MA stack stays bullish, stack up to seven independent brackets.",
        "四均线呈多头排列时最多叠加七份独立仓位，每份自带止盈止损。",
        {"short_p": 5, "mid_short_p": 10, "mid_p": 20, "long_p": 60,
         "take_profit_pct": 0.03, "stop_loss_pct": 0.10, "max_positions": 7,
         "enable_long": True, "enable_short": False},
    ),
    "dca_grid": _CatalogRow(
        "DCA Grid", "DCA网格策略", StrategyCategory.TREND,
        "MA golden cross opens the first tranche; grid ladders average down with volume martingale, stop loss guards against ruin.",
        "均线金叉后首笔买入，随后按网格分批加仓且加仓量倍投；带止损防深套与爆仓。",
        {"fast_ma": 10, "slow_ma": 30, "ma_type": "ema",
         "base_size": 5.0, "max_trades": 9,
         "grid_spacing": 0.004, "grid_price_scale": 1.19, "grid_volume_scale": 1.8,
         "take_profit_pct": 0.015, "stop_loss_pct": 0.05,
         "enable_long": True, "enable_short": False},
    ),
    "keltner_v2": _CatalogRow(
        "Keltner V2", "肯特纳通道增强版", StrategyCategory.TREND,
        "Keltner channel with ADX gating and open-price gap protection; exits only at the opposite band or the 4% trail.",
        "肯特纳通道增强版：ADX 过滤趋势强度并用开盘价防跳空；跌穿对轨才平仓让利润奔跑，另设 4% 移动止损防黑天鹅。",
        {"ma_length": 20, "atr_length": 20, "mult": 2.0,
         "adx_length": 14, "adx_threshold": 25.0,
         "stop_pct": 0.04, "tp_pct": 0.10,
         "enable_long": True, "enable_short": True},
    ),

    # ---------------------------- momentum ------------------------------
    "macd_momentum": _CatalogRow(
        "MACD Momentum", "MACD动量", StrategyCategory.MOMENTUM,
        "Read MACD histogram flips as acceleration and deceleration cues.",
        "把 MACD 柱状图翻正翻负视作动量加速与减速的进出场提示。",
        {"macd_fast": 12, "macd_slow": 26, "macd_signal": 9},
    ),
    "rsi_momentum": _CatalogRow(
        "RSI Momentum", "RSI动量", StrategyCategory.MOMENTUM,
        "Buy oversold dips and unload into overbought stretches on fixed RSI bands.",
        "按固定 RSI 区间低吸高抛：跌进超卖区买入，冲进超买区卖出。",
        {"rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70},
    ),
    "stochastic_momentum": _CatalogRow(
        "Stochastic Momentum", "KDJ动量", StrategyCategory.MOMENTUM,
        "Stochastic %K against %D crosses, triple-smoothed.",
        "随机指标 %K 与 %D 的交叉捕捉动量，附带平滑。",
        {"stoch_k": 14, "stoch_d": 3, "stoch_smooth": 3},
    ),
    "kdj_momentum": _CatalogRow(
        "KDJ Momentum", "KDJ动量", StrategyCategory.MOMENTUM,
        "J-line crossings fire the entries in this KDJ variant.",
        "以 KDJ 的 J 线交叉作为触发器的动量打法。",
        {"kdj_n": 9, "kdj_m1": 3, "kdj_m2": 3},
    ),
    "rsi_ma_delta": _CatalogRow(
        "RSI MA Delta", "RSI偏离均线策略", StrategyCategory.MOMENTUM,
        "Smooth the gap between RSI and its own moving average; zero-line crosses set direction.",
        "RSI 减去其均线所得差值经平滑，围绕零轴的上穿下穿决定方向。",
        {"rsi_len": 14, "ma_len1": 14, "ma_len2": 7,
         "ma_type": "ema", "rsi_src": "close",
         "tp_pct": 0.05, "sl_pct": 0.03,
         "enable_long": True, "enable_short": False},
    ),
    "p_signal": _CatalogRow(
        "P-Signal", "P信号熵策略", StrategyCategory.MOMENTUM,
        "Kolmogorov entropy gauge erf(SMA(dp)/sigma(dp)/sqrt(2)); buy while negative and turning up.",
        "柯尔莫哥洛夫概率熵信号 erf(SMA(Δp)/σ(Δp)/√2)，负值且回升阶段做多。",
        {"n_bars": 9, "b_smooth": True,
         "tp_pct": 0.05, "sl_pct": 0.03,
         "enable_long": True, "enable_short": False},
    ),
    "tsmom_vol_target": _CatalogRow(
        "TS Momentum + Vol Target", "时序动量 + 波动率目标策略", StrategyCategory.MOMENTUM,
        "Own the asset when its trailing return is positive, shed exposure when negative, sized by realized-vol targeting.",
        "过去 lookback 根收益为正则持有多头、为负则减仓，仓位随已实现波动率上升而收缩。",
        {"lookback": 90, "entry_threshold": 0.0, "exit_threshold": 0.0,
         "vol_target": 0.20, "vol_lookback": 30,
         "enable_long": True, "enable_short": False},
        ("BTC/USDT", "ETH/USDT", "SOL/USDT"), "1d",
    ),
    "cross_sectional_momentum": _CatalogRow(
        "Cross-Sectional Momentum", "横截面动量多空策略", StrategyCategory.MOMENTUM,
        "Rank the primary symbol's trailing return inside the fed universe and trade only the quantile tails.",
        "把主标的过去收益放进横截面宇宙排序，只做头尾分位，掉出分位即平仓。",
        {"lookback": 20, "skip_bars": 0,
         "top_quantile": 0.2, "bottom_quantile": 0.2,
         "enable_long": True, "enable_short": False},
        ("BTC/USDT", "ETH/USDT", "SOL/USDT"), "1d",
    ),
    "sentiment": _CatalogRow(
        "Sentiment Analysis", "情绪分析策略", StrategyCategory.MOMENTUM,
        "Misnamed: wired rules are plain RSI momentum (enter above 55, leave below 45); sentiment_config stays an unimplemented stub.",
        "名不副实：实际接线是纯 RSI 动量（大于 55 入场、小于 45 离场），sentiment_config 只是占位配置。",
        {"sentiment_threshold_positive": 0.5, "sentiment_threshold_negative": -0.5,
         "lookback_period": 24, "aggregation_method": "mean"},
    ),

    # -------------------------- mean reversion --------------------------
    "rsi_reversal": _CatalogRow(
        "RSI Mean Reversion", "RSI均值回归", StrategyCategory.REVERSION,
        "Fade stretched RSI readings back toward equilibrium.",
        "在 RSI 极端读数处反向下单，博价格向均衡位置修复。",
        {"rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70},
    ),
    "bollinger_reversal": _CatalogRow(
        "Bollinger Band Reversion", "布林带均值回归", StrategyCategory.REVERSION,
        "Snap-back trades launched from Bollinger envelope extremes.",
        "价格撞到布林带上下轨后向中轨回归的反转交易。",
        {"bb_period": 20, "bb_std": 2},
    ),
    "kdj_reversal": _CatalogRow(
        "KDJ Mean Reversion", "KDJ均值回归", StrategyCategory.REVERSION,
        "KDJ poking into extreme territory triggers the counter-trade.",
        "KDJ 闯入超买超卖极值区后反手开仓。",
        {"kdj_n": 9, "kdj_m1": 3, "kdj_m2": 3,
         "kdj_oversold": 20, "kdj_overbought": 80},
    ),
    "stochastic_reversal": _CatalogRow(
        "Stochastic Reversal", "随机指标均值回归", StrategyCategory.REVERSION,
        "Stochastic extremes serve as the reversion trigger here.",
        "以随机指标的极值读数作为均值回归触发器。",
        {"stoch_k": 14, "stoch_d": 3,
         "stoch_oversold": 20, "stoch_overbought": 80},
    ),
    "ma_extended_runs": _CatalogRow(
        "MA Extended Runs", "均线连续偏离策略", StrategyCategory.REVERSION,
        "After N straight bars clear an MA, trade the cross back through it; exits stack time, breakout-lookback and ATR layers.",
        "价格连续 N 根越过均线后回落下穿即抄底；出场叠加时限、突破回看与 ATR 止损三层。",
        {"ma_len": 29, "ma_type": "sma", "deviation": 14,
         "hold_bars": 4, "breakthrough_lookback": 34,
         "atr_len": 43, "atr_mult": 2.871,
         "stop_loss_pct": 0.12, "take_profit_pct": 0.96,
         "enable_long": True, "enable_short": False},
    ),
    "vwap_fibo_bands": _CatalogRow(
        "VWAP Fibo Bands", "VWAP斐波那契通道策略", StrategyCategory.REVERSION,
        "Weekly anchored VWAP wrapped in Fibonacci channel bands; fade band breaks back into the anchor.",
        "每周一锚定重置的 VWAP 外包斐波那契通道，破轨后向 VWAP 回归了结。",
        {"reset_hour": 8, "reset_day": 0,
         "fibo_inner": 1.618, "fibo_outer": 2.618, "min_bars": 4,
         "tp_pct": 0.03, "sl_pct": 0.05,
         "enable_long": True, "enable_short": True},
    ),
    "pairs_trading": _CatalogRow(
        "Pairs Trading", "配对交易", StrategyCategory.REVERSION,
        "Two-leg statistical arbitrage: OLS hedge ratio, spread z-score entries, Hurst screen for reversion quality.",
        "双标的统计套利：OLS 对冲比定比例、价差 z 值定进出、Hurst 检验把关均值回复属性。",
        {"entry_z": 2.0, "exit_z": 0.5,
         "hurst_threshold": 0.5, "lookback": 20},
        ("BTC/USDT", "ETH/USDT"), "1h",
        {"stop_loss_pct": 0.05, "take_profit_pct": 0.0,
         "max_position_pct": 0.2, "max_open_positions": 1},
    ),
    "limit_pick": _CatalogRow(
        "Limit Pick (Dip-Taking)", "限价接针", StrategyCategory.REVERSION,
        "Rest limit orders at ATR-scaled offsets beneath an EMA240 regime filter to catch wick reversions.",
        "以 EMA240 判方向、ATR 定偏离度，在插针位置挂限价单承接。",
        {"ema_period": 240, "atr_period": 60,
         "value_multiplier": 2.0, "min_selected_value_pct": 0.8,
         "enable_long": True, "enable_short": False},
    ),
    "reversion_cost_guard": _CatalogRow(
        "Reversion Cost Guard", "均值回归成本门卫", StrategyCategory.REVERSION,
        "Mean reversion under a friction budget: fire only when the expected snap-back clears the cost hurdle.",
        "带摩擦成本预算的均值回归：预期回归幅度盖过成本才允许下单。",
        {"ma_len": 20, "min_edge_mult": 1.0,
         "tp_pct": 0.05, "sl_pct": 0.03},
    ),

    # ----------------------------- breakout -----------------------------
    "bollinger_breakout": _CatalogRow(
        "Bollinger Band Breakout", "布林带突破", StrategyCategory.BREAKOUT,
        "Chase closes that escape the Bollinger envelope.",
        "收盘价冲出布林带轨道时顺着突破方向跟进。",
        {"bb_period": 20, "bb_std": 2},
    ),
    "atr_breakout": _CatalogRow(
        "ATR Breakout", "ATR突破", StrategyCategory.BREAKOUT,
        "Volatility-expansion escape measured in ATR units.",
        "以 ATR 度量波动扩张，越界即视为突破。",
        {"atr_period": 14, "atr_multiplier": 2.0},
    ),
    "adx_breakout": _CatalogRow(
        "ADX Volatility Breakout", "ADX波动突破", StrategyCategory.BREAKOUT,
        "Escapes count only when ADX corroborates trend strength.",
        "突破必须得到 ADX 走强的同步确认，用以过滤假突破。",
        {"adx_period": 14, "adx_threshold": 20},
    ),
    "bb_v3": _CatalogRow(
        "BB V3", "布林带升级版策略", StrategyCategory.BREAKOUT,
        "Asymmetric Bollinger rig: long side and short side each run their own band length and multiplier.",
        "非对称布林装置：做多侧与做空侧各自配备独立的轨道周期与倍数。",
        {"bb_len": 7, "buy_len": 44,
         "buy_mult": 2.664, "sell_len": 20, "sell_mult": 2.54,
         "buy_tp_pct": 0.12704, "buy_sl_pct": 0.07525,
         "sell_tp_pct": 0.06112, "sell_sl_pct": 0.0152,
         "enable_long": True, "enable_short": False},
    ),
    "tema_channel": _CatalogRow(
        "TEMA Channel", "TEMA波动率通道策略", StrategyCategory.BREAKOUT,
        "Ultra-smooth TEMA high/low channel: enter on the break, leave at the mid-line; short holding windows.",
        "TEMA 平滑高低价构成的极滑通道，破轨进场、回中轨离场，持仓窗口短。",
        {"length": 20, "mult": 2.0, "use_true_range": True,
         "tp_pct": 0.05, "sl_pct": 0.03,
         "enable_long": True, "enable_short": True},
    ),

    # ---------------------- volatility / carry --------------------------
    "atr_volatility": _CatalogRow(
        "ATR Volatility Strategy", "ATR波动率策略", StrategyCategory.VOLATILITY,
        "Participate in vol regimes with ATR doing the measuring.",
        "以 ATR 为标尺参与波动行情的波动率型策略。",
        {"atr_period": 14, "atr_multiplier": 2.5},
    ),
    "adx_range": _CatalogRow(
        "ADX Range Strategy", "ADX震荡策略", StrategyCategory.VOLATILITY,
        "Range playbook switched on while ADX reports a trendless tape.",
        "ADX 显示市场无趋势时，在区间内高抛低吸。",
        {"adx_period": 14, "adx_range_threshold": 20},
    ),
    "tg_spike_v1": _CatalogRow(
        "TG Spike V1", "TG消息波动率触发策略", StrategyCategory.VOLATILITY,
        "Telegram urgency-times-magnitude decay aggregation fires on strict vol-trigger z-spikes with cluster dedup and S/A/B/C tier routing.",
        "TG 消息按 urgency 与 magnitude 衰减聚合，严格 z 阈值触发波动尖峰，簇内去重并按 S/A/B/C 分级定仓。",
        {"z_threshold": 2.0, "spike_window": 20,
         "spike_min_periods": 20, "dedup_gap_bars": 24,
         "tg_db_path": "internal-archive",
         "tier_S_pos_mult": 1.0, "tier_A_pos_mult": 0.7,
         "tier_B_pos_mult": 0.5, "tier_C_ETH_REG_pos_mult": 0.5,
         "enable_long": False, "enable_short": True},
        ("BTC/USDT", "ETH/USDT", "SOL/USDT"), "4h",
        {
            "BTC/USDT": {"take_profit_pct": 0.05, "stop_loss_pct": 0.0, "hold_bars": 36},
            "ETH/USDT": {"take_profit_pct": 0.0, "stop_loss_pct": 0.03, "hold_bars": 24},
            "SOL/USDT": {"take_profit_pct": 0.0, "stop_loss_pct": 0.0, "hold_bars": 24},
            "max_position_pct": 0.5,
            "max_open_positions": 1,
        },
    ),
    "funding_arb": _CatalogRow(
        "Funding Arbitrage", "资金费率套利", StrategyCategory.VOLATILITY,
        "Collect the funding cheque: long spot financed by a short perp leg whenever funding clears the hurdle.",
        "资金费率跨过门槛时多现货、空永续，赚取资金费 carry。",
        {"funding_threshold": 0.0005, "exit_threshold": 0.0001, "max_hold_bars": 60},
    ),
    "basis_carry": _CatalogRow(
        "Basis Carry", "基差套利", StrategyCategory.VOLATILITY,
        "Harvest quarterly-futures basis above threshold; unwind at convergence or reversal.",
        "基差率超过阈值时多现货、空季月合约，基差收敛或反转时平仓。",
        {"basis_threshold": 0.02, "exit_basis": 0.005},
    ),

    # ----------------------- timing / calendar --------------------------
    "calendar_effect": _CatalogRow(
        "Calendar Effect", "日历效应策略", StrategyCategory.TIMING,
        "Fixed monthly rotation: accumulate on one date, distribute on another.",
        "每月固定日期买入、另一固定日期卖出的日历轮动打法。",
        {"buy_day": 25, "sell_day": 6, "hold_max_days": 15,
         "tp_pct": 0.10, "sl_pct": 0.05,
         "enable_long": True, "enable_short": False},
    ),
    "intraday_seasonality": _CatalogRow(
        "Intraday Seasonality", "隔夜时段效应策略", StrategyCategory.TIMING,
        "Hold one fixed UTC window per day to harvest systematic intraday drift; price data only.",
        "每天只在固定 UTC 时段持仓，收割稳定的日内时段性漂移，仅需价格数据。",
        {"entry_hour": 22, "exit_hour": 0,
         "max_hold_hours": 24, "enable_long": True},
        ("BTC/USDT",),
    ),
    "rebalance_premium": _CatalogRow(
        "Rebalance Premium", "再平衡溢价策略", StrategyCategory.TIMING,
        "Long the daily equal-weight rebalancing basket against the drifting buy-and-hold basket to pocket the volatility-harvest premium.",
        "做多每日等权再平衡篮子相对买入持有的漂移差额，收取波动率收割溢价。",
        {"lookback": 20, "entry_threshold": 0.01, "exit_threshold": 0.0,
         "enable_long": True, "enable_short": False},
        ("BTC/USDT", "ETH/USDT", "SOL/USDT"), "1d",
    ),
    "january_barometer": _CatalogRow(
        "January Barometer", "一月晴雨表策略", StrategyCategory.TIMING,
        "January's return alone decides whether the rest of the year is invested or flat.",
        "仅凭一月的涨跌决定全年持仓还是空仓的晴雨表规则。",
        {"january_threshold": 0.0, "max_hold_days": 60, "enable_long": True},
        ("BTC/USDT", "ETH/USDT", "SOL/USDT"), "1d",
    ),

    # ------------------------------- AI ---------------------------------
    "llm_signal": _CatalogRow(
        "LLM Signal Strategy", "LLM 信号策略", StrategyCategory.AI,
        "A language model reads kline-plus-indicator context and returns a structured long/short/hold verdict with confidence and size hints.",
        "语言模型阅读 K 线与指标上下文，输出带置信度与仓位建议的结构化多空结论。",
        {"llm_provider": "deepseek", "timeframe": "15m",
         "data_points": 96, "max_position_pct": 0.2, "signal_ttl_s": 300},
    ),

    # -------------------- v4 preset upgrades (2026-08) -------------------
    "rsi_momentum_continuation": _CatalogRow(
        "RSI Momentum Continuation", "RSI动量延续", StrategyCategory.TREND,
        "Hold the long while RSI keeps clearing the continuation floor; step aside once momentum tires.",
        "RSI 守在延续阈值之上就继续持有多头，动能衰竭跌破离场线即撤退。",
        {"rsi_len": 14, "continue_threshold": 70.0, "exit_threshold": 55.0,
         "tp_pct": 0.05, "sl_pct": 0.03},
    ),
    "er_state_machine": _CatalogRow(
        "ER State Machine", "ER效率比率状态机", StrategyCategory.TREND,
        "Efficiency Ratio flips the machine between noise and trend states; positions exist only in the trend state.",
        "效率比率驱动状态机在噪声态与趋势态之间切换，只有趋势态才持仓。",
        {"er_period": 14, "entry_threshold": 0.5, "exit_threshold": 0.3,
         "tp_pct": 0.05, "sl_pct": 0.03},
    ),
    "chop_guard": _CatalogRow(
        "CHOP Guard Trend", "CHOP门卫趋势", StrategyCategory.TREND,
        "The Choppiness Index mans the gate: trend trades pass only while the tape stays orderly.",
        "由 Choppiness 指数把门：盘面有序才放行趋势单，转震荡立即收手。",
        {"chop_period": 14, "ma_len": 20,
         "tp_pct": 0.05, "sl_pct": 0.03},
    ),
}


# Registration order is part of the observable surface (UI listings and dict
# iteration both follow it), so the thematic grouping above feeds a fixed
# sequence here. Any new catalog key must be appended to this tuple.
_CATALOG_ORDER: tuple[str, ...] = (
    # trend-following openers
    "sma_cross", "ema_cross", "adx_trend", "atr_trailing",
    # momentum block
    "macd_momentum", "rsi_momentum", "stochastic_momentum", "kdj_momentum",
    # reversion block
    "rsi_reversal", "bollinger_reversal", "kdj_reversal", "stochastic_reversal",
    # breakout block
    "bollinger_breakout", "atr_breakout", "adx_breakout",
    # volatility block
    "atr_volatility", "adx_range", "kdj_trend",
    # sentiment (misnamed RSI momentum)
    "sentiment",
    # extended runs / grid
    "ma_extended_runs", "dca_grid",
    # asymmetric bollinger family
    "bb_v3", "rsi_ma_delta", "bb_atr_trend", "dmi_delta",
    # entropy / supertrend / volume-weighted
    "p_signal", "stoch_supertrend", "vw_ema_cross",
    # anchored-vwap and triple-EMA structures
    "vwap_fibo_bands", "ema_triple_cross",
    # calendar and session windows
    "calendar_effect", "intraday_seasonality", "rebalance_premium",
    # absorbed systematic-trading presets
    "tsmom_vol_target", "cross_sectional_momentum", "january_barometer",
    # channel structures
    "tema_channel", "keltner_v2",
    # telegram news-spike
    "tg_spike_v1",
    # pyramid and carry structures
    "pyramid_ma", "funding_arb", "basis_carry",
    # statistical arbitrage and dip-taking
    "pairs_trading", "limit_pick",
    # AI verdicts and v4 preset upgrades
    "llm_signal", "rsi_momentum_continuation", "er_state_machine",
    "chop_guard", "reversion_cost_guard",
)

# Catalog rows that are listed but resolve to no implementation in any path:
# not a preset module, not a seeded YAML rule set, not one of the native config
# types. They are kept in the catalog because the intent is recorded and the
# work is planned, but they are not offered as runnable -- a run would compile
# a condition-less strategy and complete with zero trades.
#
# ``pairs_trading``/``funding_arb``/``basis_carry`` are the subtlest of the set:
# they do have preset classes, but those read a second instrument through
# ``set_secondary_data()`` and every non-test caller of that method is absent,
# so they return HOLD for the life of the run rather than raising.
_UNIMPLEMENTED_KEYS: frozenset[str] = frozenset(
    {
        "basis_carry",
        "funding_arb",
        "limit_pick",
        "llm_signal",
        "pairs_trading",
        "tg_spike_v1",
    }
)


def _build_catalog() -> dict[str, StrategyMetadata]:
    """Expand rows into descriptors following :data:`_CATALOG_ORDER`.

    Raises at import time when the order table and the row table disagree --
    a cheap guard against someone adding a row but forgetting the sequence.
    """
    if set(_CATALOG_ORDER) != set(_CATALOG):
        missing = sorted(set(_CATALOG) - set(_CATALOG_ORDER))
        unknown = sorted(set(_CATALOG_ORDER) - set(_CATALOG))
        raise RuntimeError(
            f"catalog order/rows mismatch: unsequenced={missing} unknown={unknown}"
        )
    # A stale name here would silently mark nothing, leaving the row advertised
    # as runnable while it cannot run -- the exact failure this set exists to
    # prevent, so the typo has to be fatal.
    if not _UNIMPLEMENTED_KEYS <= set(_CATALOG):
        raise RuntimeError(
            f"unimplemented keys absent from catalog: "
            f"{sorted(_UNIMPLEMENTED_KEYS - set(_CATALOG))}"
        )
    return {
        key: StrategyMetadata(
            id=_template_id(key),
            name=_CATALOG[key].title,
            name_zh=_CATALOG[key].title_zh,
            category=_CATALOG[key].bucket,
            description=_CATALOG[key].blurb,
            description_zh=_CATALOG[key].blurb_zh,
            default_params=dict(_CATALOG[key].params),
            risk_params=(
                dict(_CATALOG[key].risk_book)
                if _CATALOG[key].risk_book is not None
                else _standard_risk_book()
            ),
            symbols=list(_CATALOG[key].universe) if _CATALOG[key].universe else ["BTC/USDT"],
            interval=_CATALOG[key].bar_size,
            runnable=key not in _UNIMPLEMENTED_KEYS,
        )
        for key in _CATALOG_ORDER
    }


STRATEGY_TEMPLATES: dict[str, StrategyMetadata] = _build_catalog()


# ---------------------------------------------------------------------------
# Strategy Factory
# ---------------------------------------------------------------------------

# Template ids whose configs compile natively (everything else maps to "custom").
_NATIVE_CONFIG_TYPES: MappingProxyType[str, str] = MappingProxyType({
    "sma_cross": "sma_cross",
    "rsi_reversal": "rsi_reversal",
    "macd_momentum": "macd",
    "sentiment": "sentiment",
})


class StrategyFactory:
    """Front door for the pre-built template lineup.

    Responsibilities: browse the catalog, stamp out ready-to-compile configs,
    hand back live runtimes, and funnel history through a runtime to collect
    signals.
    """

    @staticmethod
    def list_strategies(category: StrategyCategory | None = None) -> list[dict[str, Any]]:
        """Summarize the catalog, optionally narrowed to one category."""
        return [
            {
                "id": key,
                "name": meta.name,
                "name_zh": meta.name_zh,
                "category": meta.category.value,
                "description": meta.description,
                "description_zh": meta.description_zh,
                "default_params": meta.default_params,
            }
            for key, meta in STRATEGY_TEMPLATES.items()
            if category is None or meta.category == category
        ]

    @staticmethod
    def get_metadata(strategy_id: str) -> StrategyMetadata | None:
        """Fetch the descriptor for ``strategy_id``, or None when absent."""
        return STRATEGY_TEMPLATES.get(strategy_id)

    @staticmethod
    def create(
        strategy_id: str,
        parameters: dict[str, Any] | None = None,
        symbols: list[str] | None = None,
        interval: str | None = None,
    ) -> EngineStrategyConfig:
        """Stamp a template descriptor into a compilable config.

        Default parameters merge with ``parameters`` (overrides win);
        ``symbols`` and ``interval`` replace the descriptor values when given.

        Raises:
            ValueError: when ``strategy_id`` is not part of the catalog.
        """
        meta = STRATEGY_TEMPLATES.get(strategy_id)
        if meta is None:
            known = ", ".join(sorted(STRATEGY_TEMPLATES))
            raise ValueError(
                f"Strategy '{strategy_id}' not found. Available: [{known}]"
            )

        merged: dict[str, Any] = dict(meta.default_params)
        if parameters:
            merged.update(parameters)

        return EngineStrategyConfig(
            id=meta.id,
            name=meta.name,
            type=_NATIVE_CONFIG_TYPES.get(strategy_id, "custom"),
            description=meta.description,
            symbols=symbols or meta.symbols,
            interval=interval or meta.interval,
            parameters=merged,
            risk_params=meta.risk_params,
        )

    @staticmethod
    def compile(
        strategy_id: str,
        parameters: dict[str, Any] | None = None,
        symbols: list[str] | None = None,
        interval: str | None = None,
    ) -> StrategyRuntime:
        """Build the config for ``strategy_id`` and return a running runtime."""
        config = StrategyFactory.create(strategy_id, parameters, symbols, interval)
        return StrategyRuntime(compile_config(config))

    @staticmethod
    def build_strategy_signals(
        strategy_or_id: StrategyRuntime | EngineStrategyConfig | str,
        data: pd.DataFrame | list[KlineEvent],
    ) -> list[Signal]:
        """Dispatch history through a strategy in whatever form it arrives.

        ``strategy_or_id`` may be a catalog id (compiled here), a ready config
        (compiled here), or an existing runtime (reused). DataFrame input is
        converted to kline events first; the runtime resets before replay.
        Every non-None per-bar emission lands in the returned list.
        """
        stream = (
            StrategyFactory._dataframe_to_klines(data)
            if isinstance(data, pd.DataFrame)
            else data
        )

        if isinstance(strategy_or_id, str):
            runtime = StrategyFactory.compile(strategy_or_id)
        elif isinstance(strategy_or_id, EngineStrategyConfig):
            runtime = StrategyRuntime(compile_config(strategy_or_id))
        else:
            runtime = strategy_or_id

        runtime.reset()

        collected: list[Signal] = []
        for candle in stream:
            emitted = runtime.on_kline(candle)
            if emitted is not None:
                collected.append(emitted)
        return collected

    @staticmethod
    def _dataframe_to_klines(frame: pd.DataFrame) -> list[KlineEvent]:
        """Project an OHLCV frame onto kline events row by row."""
        return [
            KlineEvent(
                exchange=record.get("exchange", "binance"),
                symbol=record.get("symbol", "BTC/USDT"),
                timestamp=int(record["timestamp"]),
                interval=record.get("interval", "1h"),
                open=float(record["open"]),
                high=float(record["high"]),
                low=float(record["low"]),
                close=float(record["close"]),
                volume=float(record["volume"]),
                closed=record.get("closed", True),
            )
            for record in frame.to_dict("records")
        ]

    @staticmethod
    def categories() -> list[StrategyCategory]:
        """Every category value in declaration order."""
        return [*StrategyCategory]

    @staticmethod
    def category_count() -> dict[str, int]:
        """How many templates sit in each category."""
        tally = {bucket.value: 0 for bucket in StrategyCategory}
        for meta in STRATEGY_TEMPLATES.values():
            tally[meta.category.value] += 1
        return tally


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def create_pairs_strategy(config: EngineStrategyConfig) -> PairsTradingStrategy:
    """Build a :class:`PairsTradingStrategy` straight from a config.

    Deliberately outside :meth:`StrategyFactory.create`, which yields configs
    rather than instances -- pairs trading runs through the
    ``BacktestStrategyTemplate`` path instead of the compiler/runtime path.
    Call ``set_secondary_data()`` before ``run_backtest()``.
    """
    return PairsTradingStrategy.from_config(config)


def create_intraday_seasonality_strategy(
    config: EngineStrategyConfig,
) -> IntradaySeasonalityStrategy:
    """Build an intraday-seasonality template instance from a config.

    The config is only read, never mutated; feed your own kline stream to
    ``run_backtest()`` afterwards.
    """
    return IntradaySeasonalityStrategy.from_config(config)


def create_rebalance_premium_strategy(
    config: EngineStrategyConfig,
) -> RebalancePremiumStrategy:
    """Build a rebalance-premium template instance from a config.

    Supply the universe close series via ``set_universe_data()`` prior to
    ``run_backtest()``.
    """
    return RebalancePremiumStrategy.from_config(config)


def create_tsmom_vol_target_strategy(
    config: EngineStrategyConfig,
) -> TsmomVolTargetStrategy:
    """Build a TS-momentum/vol-target template instance from a config.

    Read-only helper: the config is consumed as-is and backtesting proceeds
    on whatever kline stream the caller provides.
    """
    return TsmomVolTargetStrategy.from_config(config)


def create_cross_sectional_momentum_strategy(
    config: EngineStrategyConfig,
) -> CrossSectionalMomentumStrategy:
    """Build a cross-sectional-momentum template instance from a config.

    Read-only helper; remember to feed the peer universe before backtesting.
    """
    return CrossSectionalMomentumStrategy.from_config(config)


def create_january_barometer_strategy(
    config: EngineStrategyConfig,
) -> JanuaryBarometerStrategy:
    """Build a January-barometer template instance from a config.

    Read-only helper; the caller owns the kline stream passed to
    ``run_backtest()``.
    """
    return JanuaryBarometerStrategy.from_config(config)


def create_strategy(strategy_id: str, **kwargs: Any) -> EngineStrategyConfig:
    """Module-level shortcut for :meth:`StrategyFactory.create`."""
    return StrategyFactory.create(strategy_id, **kwargs)


def build_signals(strategy_or_id: Any, data: Any) -> list[Signal]:
    """Module-level shortcut for :meth:`StrategyFactory.build_strategy_signals`."""
    return StrategyFactory.build_strategy_signals(strategy_or_id, data)


def list_available_strategies() -> list[str]:
    """All catalog keys, in registration order."""
    return list(STRATEGY_TEMPLATES)


# ---------------------------------------------------------------------------
# Vectorized ternary generators
# ---------------------------------------------------------------------------

# Qbot-flavored ternary codes.
SIGNAL_BUY = 1
SIGNAL_SELL = 0
SIGNAL_HOLD = -1


def _as_series(values: np.ndarray | pd.Series) -> pd.Series:
    """Wrap ndarray input into a Series; Series passes through untouched."""
    return values if isinstance(values, pd.Series) else pd.Series(values)


def _ternary_frame(
    index: pd.Index,
    buy_rows: pd.Series,
    sell_rows: pd.Series,
    **annotation_columns: pd.Series,
) -> pd.DataFrame:
    """Assemble the standard generator output: ternary signal + annotations.

    ``buy_rows``/``sell_rows`` are boolean masks already indexed like the
    data; where neither fires the frame carries HOLD.
    """
    picked = np.select(
        [buy_rows.to_numpy(dtype=bool), sell_rows.to_numpy(dtype=bool)],
        [SIGNAL_BUY, SIGNAL_SELL],
        default=SIGNAL_HOLD,
    )
    payload: dict[str, Any] = {"signal": pd.Series(picked, index=index)}
    payload.update(annotation_columns)
    return pd.DataFrame(payload)


def generate_crossover_signals(
    fast: np.ndarray | pd.Series,
    slow: np.ndarray | pd.Series,
    index: pd.Index | None = None,
) -> pd.DataFrame:
    """Ternary frame for a dual-line crossover study.

    BUY when ``fast`` rises across ``slow``; SELL on the downward cross;
    everything else HOLD. Output columns: ``signal``, ``fast``, ``slow``.
    """
    f = _as_series(fast)
    s = _as_series(slow)
    out_index = f.index if index is None else index

    rose_through = (f > s) & (f.shift(1) <= s.shift(1))
    fell_through = (f < s) & (f.shift(1) >= s.shift(1))
    return _ternary_frame(out_index, rose_through, fell_through, fast=f, slow=s)


def generate_threshold_signals(
    indicator: np.ndarray | pd.Series,
    lower: float,
    upper: float,
    index: pd.Index | None = None,
) -> pd.DataFrame:
    """Ternary frame for band-threshold studies.

    BUY below ``lower`` (oversold side), SELL above ``upper`` (overbought
    side). Output columns: ``signal``, ``indicator``.
    """
    ind = _as_series(indicator)
    out_index = ind.index if index is None else index
    return _ternary_frame(out_index, ind < lower, ind > upper, indicator=ind)


def generate_breakout_signals(
    price: np.ndarray | pd.Series,
    upper: np.ndarray | pd.Series,
    lower: np.ndarray | pd.Series,
    index: pd.Index | None = None,
) -> pd.DataFrame:
    """Ternary frame for channel-break studies.

    BUY above the ``upper`` rail, SELL below the ``lower`` rail.
    Output columns: ``signal``, ``price``.
    """
    px = _as_series(price)
    hi = _as_series(upper)
    lo = _as_series(lower)
    out_index = px.index if index is None else index
    return _ternary_frame(out_index, px > hi, px < lo, price=px)


def generate_mean_reversion_signals(
    zscore: np.ndarray | pd.Series,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    index: pd.Index | None = None,
) -> pd.DataFrame:
    """Ternary frame for z-score reversion studies.

    BUY past ``-entry_z``, SELL past ``+entry_z``. ``exit_z`` exists for
    interface compatibility only -- both sides key off the symmetric
    ``entry_z`` band. Output columns: ``signal``, ``zscore``.
    """
    del exit_z  # reserved parameter; see docstring
    z = _as_series(zscore)
    out_index = z.index if index is None else index
    return _ternary_frame(out_index, z < -entry_z, z > entry_z, zscore=z)


def generate_sentiment_signals(
    sentiment_scores: np.ndarray | pd.Series,
    positive_threshold: float = 0.5,
    negative_threshold: float = -0.5,
    index: pd.Index | None = None,
) -> pd.DataFrame:
    """Ternary frame keyed off externally computed sentiment scores.

    BUY above ``positive_threshold``, SELL below ``negative_threshold``. Score
    production itself lives outside this package. Output columns: ``signal``,
    ``sentiment``.
    """
    mood = _as_series(sentiment_scores)
    out_index = mood.index if index is None else index
    return _ternary_frame(out_index, mood > positive_threshold, mood < negative_threshold,
                          sentiment=mood)


# ---------------------------------------------------------------------------
# Multi-indicator fusion recipes
# ---------------------------------------------------------------------------

FUSION_STRATEGIES: dict[str, dict[str, Any]] = {
    "rsi_macd_confluence": {
        "description": "RSI and the MACD histogram must agree before anything prints",
        "params": {
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
        },
        "logic": "BUY needs RSI under the oversold line with a positive histogram; "
                 "SELL is the mirrored overbought/negative-histogram state.",
    },
    "macd_adx_filter": {
        "description": "MACD crossings taken only while ADX certifies real trend strength",
        "params": {
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "adx_period": 14,
            "adx_threshold": 25,
        },
        "logic": "BUY on a MACD bull cross with ADX over the threshold; SELL on the "
                 "bear cross under the same strength requirement.",
    },
    "bollinger_rsi_combo": {
        "description": "Band extreme plus oscillator agreement in one gate",
        "params": {
            "bb_period": 20,
            "bb_std": 2.0,
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
        },
        "logic": "BUY when close sits beneath the lower band with RSI oversold; SELL "
                 "when close tops the upper band with RSI overbought.",
    },
    "triple_confirmation": {
        "description": "All three gauges -- RSI, MACD, Bollinger -- must line up",
        "params": {
            "rsi_period": 14,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "bb_period": 20,
            "bb_std": 2.0,
        },
        "logic": "BUY demands oversold RSI, positive histogram and a close under the "
                 "lower band simultaneously; SELL is the exact reflection.",
    },
}


def get_fusion_strategies() -> list[str]:
    """Keys of every registered fusion recipe."""
    return list(FUSION_STRATEGIES)


def get_fusion_strategy_info(name: str) -> dict[str, Any] | None:
    """Recipe card (description/params/logic) for one fusion key."""
    return FUSION_STRATEGIES.get(name)


# ---------------------------------------------------------------------------
# Fusion implementations (private)
# ---------------------------------------------------------------------------


def _gate_frame(index: pd.Index, buy_rows: pd.Series, sell_rows: pd.Series,
                **annotations: pd.Series) -> pd.DataFrame:
    """Shared assembler for the fusion builders (same ternary contract)."""
    picked = np.select(
        [buy_rows.to_numpy(dtype=bool), sell_rows.to_numpy(dtype=bool)],
        [SIGNAL_BUY, SIGNAL_SELL],
        default=SIGNAL_HOLD,
    )
    payload: dict[str, Any] = {"signal": pd.Series(picked, index=index)}
    payload.update(annotations)
    return pd.DataFrame(payload)


def _macd_from(book: dict[str, Any], frame: pd.DataFrame):
    from selene_space_strategy.indicators.macd import MACD

    return MACD(
        period=book["macd_slow"],
        fast_period=book["macd_fast"],
        signal_period=book["macd_signal"],
    ).calculate_full(frame)


def _fusion_rsi_macd(frame: pd.DataFrame, book: dict[str, Any]) -> pd.DataFrame:
    """Confluence gate: oscillator trough plus positive histogram, and mirror."""
    from selene_space_strategy.indicators.rsi import RSI

    osc = RSI(period=book["rsi_period"]).calculate(frame)
    mac = _macd_from(book, frame)

    return _gate_frame(
        frame.index,
        (osc < book["rsi_oversold"]) & (mac.histogram > 0),
        (osc > book["rsi_overbought"]) & (mac.histogram < 0),
        rsi=osc,
        macd_line=mac.macd_line,
        macd_signal=mac.signal_line,
        macd_hist=mac.histogram,
    )


def _fusion_macd_adx(frame: pd.DataFrame, book: dict[str, Any]) -> pd.DataFrame:
    """MACD crosses admitted solely while ADX reads above threshold."""
    from selene_space_strategy.indicators.adx import ADX

    mac = _macd_from(book, frame)
    dmi = ADX(period=book["adx_period"]).calculate_full(frame)

    line, trip = mac.macd_line, mac.signal_line
    bull_cross = (line > trip) & (line.shift(1) <= trip.shift(1))
    bear_cross = (line < trip) & (line.shift(1) >= trip.shift(1))
    sturdy = dmi.adx > book["adx_threshold"]

    return _gate_frame(
        frame.index,
        bull_cross & sturdy,
        bear_cross & sturdy,
        macd_line=line,
        macd_signal=trip,
        macd_hist=mac.histogram,
        adx=dmi.adx,
        plus_di=dmi.plus_di,
        minus_di=dmi.minus_di,
    )


def _fusion_bollinger_rsi(frame: pd.DataFrame, book: dict[str, Any]) -> pd.DataFrame:
    """Band pierce confirmed by an agreeing oscillator reading."""
    from selene_space_strategy.indicators.bbands import BollingerBands
    from selene_space_strategy.indicators.rsi import RSI

    envelope = BollingerBands(
        period=book["bb_period"], num_std=book["bb_std"],
    ).calculate_full(frame)
    osc = RSI(period=book["rsi_period"]).calculate(frame)
    close = frame["close"]

    return _gate_frame(
        frame.index,
        (close < envelope.lower) & (osc < book["rsi_oversold"]),
        (close > envelope.upper) & (osc > book["rsi_overbought"]),
        bb_upper=envelope.upper,
        bb_middle=envelope.middle,
        bb_lower=envelope.lower,
        rsi=osc,
    )


def _fusion_triple(frame: pd.DataFrame, book: dict[str, Any]) -> pd.DataFrame:
    """Three-way alignment: oscillator, histogram and envelope all concur.

    Oversold/overbought lines fall back to 30/70 because this recipe's default
    book omits them.
    """
    from selene_space_strategy.indicators.bbands import BollingerBands
    from selene_space_strategy.indicators.rsi import RSI

    soft_line = book.get("rsi_oversold", 30)
    hot_line = book.get("rsi_overbought", 70)

    osc = RSI(period=book["rsi_period"]).calculate(frame)
    mac = _macd_from(book, frame)
    envelope = BollingerBands(
        period=book["bb_period"], num_std=book["bb_std"],
    ).calculate_full(frame)
    close = frame["close"]

    return _gate_frame(
        frame.index,
        (osc < soft_line) & (mac.histogram > 0) & (close < envelope.lower),
        (osc > hot_line) & (mac.histogram < 0) & (close > envelope.upper),
        rsi=osc,
        macd_hist=mac.histogram,
        bb_upper=envelope.upper,
        bb_lower=envelope.lower,
    )


_FUSION_BUILDERS = {
    "rsi_macd_confluence": _fusion_rsi_macd,
    "macd_adx_filter": _fusion_macd_adx,
    "bollinger_rsi_combo": _fusion_bollinger_rsi,
    "triple_confirmation": _fusion_triple,
}


def build_fusion_strategy_signals(
    name: str,
    data: pd.DataFrame,
    **overrides: Any,
) -> pd.DataFrame:
    """Run one registered fusion recipe over OHLCV history.

    Merges the recipe's default book with ``overrides``, hands the frame to
    the matching vectorized builder, and returns the ternary ``signal``
    column next to its diagnostic indicator columns.

    Raises:
        ValueError: when ``name`` is not a registered fusion key.
    """
    recipe = FUSION_STRATEGIES.get(name)
    if recipe is None:
        raise ValueError(
            f"Unknown fusion strategy '{name}'. Available: {list(FUSION_STRATEGIES)}"
        )
    book = {**recipe["params"], **overrides}
    return _FUSION_BUILDERS[name](data, book)


# ---------------------------------------------------------------------------
# Telegram news-spike bridge
# ---------------------------------------------------------------------------


def _with_datetime_index(frame: pd.DataFrame) -> pd.DataFrame:
    """Guarantee a UTC DatetimeIndex, rebuilding one from ms ``timestamp``."""
    if isinstance(frame.index, pd.DatetimeIndex):
        return frame
    if "timestamp" not in frame.columns:
        return frame
    staged = frame.copy()
    staged["_utc_ts"] = pd.to_datetime(staged["timestamp"], unit="ms", utc=True)
    return staged.set_index("_utc_ts").sort_index()


def build_tg_spike_signals(
    data: pd.DataFrame,
    symbol: str = "BTC/USDT",
    interval: str = "4h",
    tg_db_path: str = "internal-archive",
    other_symbol_spike_sets: list[set] | None = None,
    z_threshold: float = 2.0,
    strategy_id: str = "tg-spike-v1",
    as_pydantic: bool = False,
    use_event_overlay_source: bool = False,
) -> list[Signal] | list[dict[str, Any]]:
    """Replay kline history through the Telegram spike machinery.

    Batch mode on purpose: spike scoring needs the rolling z-score window, so
    streaming per-bar replay is unavailable here.

    Args:
        data: kline frame (DatetimeIndex preferred; a ms ``timestamp`` column
            converts automatically). Columns: open/high/low/close/volume.
        symbol: traded symbol, e.g. ``"ETH/USDT"``.
        interval: kline granularity of ``data``.
        tg_db_path: location of ``tg_factors.db``.
        other_symbol_spike_sets: precomputed peer spike sets enabling multi_sym
            S-tier detection; pass ``[]`` to switch that off.
        z_threshold: strict spike cutoff on the rolling z-score.
        strategy_id: tag stamped onto emitted Signal objects.
        as_pydantic: True returns typed ``Signal`` objects, False raw dicts.
        use_event_overlay_source: route to ``TGEventOverlaySource`` instead --
            de-risk-only output (never carries side/direction). Mutually
            exclusive with the legacy ``TGSpikeSource``: a session enables at
            most one, else de-risking double-applies. Default False keeps the
            legacy path (deprecated: directional alpha falsified 2026-08-13).

    Returns:
        Signals (typed or dict) whose strength is the tier position multiplier.
    """
    frame = _with_datetime_index(data)

    if use_event_overlay_source:
        from selene_space_alpha.factors.tg_event_overlay_source import TGEventOverlaySource
        overlay = TGEventOverlaySource(
            tg_db_path=tg_db_path,
            symbol=symbol,
            interval=interval,
            strategy_id=strategy_id,
        )
        return overlay.build_signals(frame) if as_pydantic else overlay.build_signal_dicts(frame)

    try:
        from selene_space_alpha.factors.tg_spike_source import TGSpikeSource
    except ImportError as exc:
        raise ImportError(
            "TGSpikeSource requires selene_space_alpha. "
            "Install alpha-core or add to PYTHONPATH."
        ) from exc

    # Regression rail: reaching this point with the overlay flag set would
    # mean both TG sources went live in one session (double de-risk).
    assert not use_event_overlay_source, (
        "TG source mutual exclusion violated: TGSpikeSource must not be built "
        "with use_event_overlay_source=True (would double de-risk)"
    )

    spike = TGSpikeSource(
        tg_db_path=tg_db_path,
        symbol=symbol,
        interval=interval,
        z_threshold=z_threshold,
        other_symbol_spike_sets=other_symbol_spike_sets or [],
        strategy_id=strategy_id,
    )
    return spike.build_signals(frame) if as_pydantic else spike.build_signal_dicts(frame)


def build_tg_spike_portfolio_signals(
    data_by_symbol: dict[str, pd.DataFrame],
    interval: str = "4h",
    tg_db_path: str = "internal-archive",
    z_threshold: float = 2.0,
    enable_tg_overlay: bool = True,
    use_event_overlay_source: bool = False,
    enable_tg_regime: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Per-symbol Telegram signals across a portfolio.

    The legacy path auto-computes peer spike sets for multi_sym (S-tier)
    detection. The event-overlay path is per-symbol de-risk only -- no
    cross-symbol tiering, no directional output.

    Args:
        data_by_symbol: mapping of symbol to its kline frame.
        interval: kline granularity shared by every frame.
        tg_db_path: location of ``tg_factors.db``.
        z_threshold: spike z-score cutoff (legacy path).
        enable_tg_overlay: apply the +/-2h SECURITY/REGULATORY overlay on the
            legacy path, mirroring live ``EventGuard.get_tg_multiplier``
            behavior; disable for A/B runs.
        use_event_overlay_source: swap in ``TGEventOverlaySource`` (de-risk
            only). Mutually exclusive with the legacy source -- never both in
            one session.
        enable_tg_regime: append ``TGVolRegimeSource`` dicts (48h x 0.85
            persistence de-risk) to each symbol's list. Orthogonal to both
            paths; consumers fold layers with per-bar ``min()``.

    Returns:
        Mapping of symbol to its signal-dict list.
    """
    frames = {sym: _with_datetime_index(df) for sym, df in data_by_symbol.items()}

    if use_event_overlay_source:
        from selene_space_alpha.factors.tg_event_overlay_source import TGEventOverlaySource
        merged: dict[str, list[dict[str, Any]]] = {}
        for sym, frame in frames.items():
            overlay = TGEventOverlaySource(
                tg_db_path=tg_db_path,
                symbol=sym,
                interval=interval,
            )
            merged[sym] = overlay.build_signal_dicts(frame)
        if enable_tg_regime:
            merged = _attach_regime_layer(merged, frames, tg_db_path, interval)
        return merged

    try:
        from selene_space_alpha.factors.tg_spike_source import (
            TGSpikeSource,
            compute_other_symbol_spike_sets,
        )
    except ImportError as exc:
        raise ImportError("TGSpikeSource requires selene_space_alpha.") from exc

    # Regression rail mirroring the single-symbol builder above.
    assert not use_event_overlay_source, (
        "TG source mutual exclusion violated: TGSpikeSource must not be built "
        "with use_event_overlay_source=True (would double de-risk)"
    )

    peer_spikes = compute_other_symbol_spike_sets(tg_db_path, frames, interval)

    merged = {}
    for sym, frame in frames.items():
        peers = [peer_spikes[other] for other in frames if other != sym]
        spike = TGSpikeSource(
            tg_db_path=tg_db_path,
            symbol=sym,
            interval=interval,
            z_threshold=z_threshold,
            other_symbol_spike_sets=peers,
            enable_tg_overlay=enable_tg_overlay,
        )
        merged[sym] = spike.build_signal_dicts(frame)
    if enable_tg_regime:
        merged = _attach_regime_layer(merged, frames, tg_db_path, interval)
    return merged


def _attach_regime_layer(
    merged: dict[str, list[dict[str, Any]]],
    frames: dict[str, pd.DataFrame],
    tg_db_path: str,
    interval: str,
) -> dict[str, list[dict[str, Any]]]:
    """Append the 48h x 0.85 volatility-regime dicts to each symbol's list.

    The regime layer stacks on top of whichever TG path produced ``merged``;
    downstream folds all layers per bar via ``min()``, matching how live
    ``EventGuard.get_position_multiplier`` collapses to 0.85 inside an active
    whitelisted window.
    """
    from selene_space_alpha.factors.tg_vol_regime_source import TGVolRegimeSource

    for sym, frame in frames.items():
        regime = TGVolRegimeSource(tg_db_path=tg_db_path, symbol=sym, interval=interval)
        merged[sym].extend(regime.build_signal_dicts(frame))
    return merged


__all__ = [
    "STRATEGY_TEMPLATES",
    "StrategyCategory",
    "StrategyFactory",
    "StrategyMetadata",
    "build_signals",
    "build_tg_spike_signals",
    "build_tg_spike_portfolio_signals",
    "create_strategy",
    "list_available_strategies",
]

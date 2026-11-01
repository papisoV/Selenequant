#!/usr/bin/env python3
"""LLM-guided parameter tuning for all strategy templates.

This script runs Bayesian optimization (via Optuna) to find optimal parameters
for all available strategy templates, using BTC K-line data from local SQLite.

Usage:
    python scripts/llm_param_tuner.py                    # 默认配置
    python scripts/llm_param_tuner.py --interval 4h      # 指定周期
    python scripts/llm_param_tuner.py --trials 50        # 更多迭代
    python scripts/llm_param_tuner.py --strategies bb_v3 rsi_ma_delta  # 指定策略

Output:
    - Console: 实时进度和结果汇总
    - JSON: 详细调参结果保存到 data/tuning_results/
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from tqdm import tqdm


def _fix_windows_console_encoding() -> None:
    """Rewrap stdout/stderr as UTF-8 on Windows consoles.

    Deliberately NOT run at import time: this module is imported by tests, and
    reassigning ``sys.stdout`` mid-session wraps whatever stream is installed
    at that moment. Under pytest that is the capture tmpfile; when the new
    wrapper is collected it closes the shared buffer, and pytest's own teardown
    then fails with ``ValueError: I/O operation on closed file``.
    """
    if sys.platform == "win32":
        import io

        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from selene_space_market.kline_db import KlineDB
from selene_space_strategy.optimizer import (
    StrategyOptimizer,
    _periods_per_year_from_interval,
)
from selene_space_strategy.template import BacktestStrategyTemplate
from selene_space_types.market import KlineEvent
from selene_space_types.routing import MarketDomain
from selene_space_types.calendars import resolve_market_domain

from research_artifacts import (
    RESEARCH_ARTIFACT_SCHEMA_VERSION,
    build_source_annotation,
)

# Import all strategy templates
from selene_space_strategy.templates.presets.bb_v3 import BBV3Strategy
from selene_space_strategy.templates.presets.rsi_ma_delta import RSIMADeltaStrategy
from selene_space_strategy.templates.presets.bb_atr_trend import BBATRTrendStrategy
from selene_space_strategy.templates.presets.dmi_delta import DMIDeltaStrategy
from selene_space_strategy.templates.presets.p_signal import PSignalStrategy
from selene_space_strategy.templates.presets.stoch_supertrend import (
    STOCHSupertrendStrategy,
)
from selene_space_strategy.templates.presets.vw_ema_cross import VWEMACrossoverStrategy
from selene_space_strategy.templates.presets.vwap_fibo_bands import (
    VWAPFiboBandsStrategy,
)
from selene_space_strategy.templates.presets.ema_triple_cross import (
    EMATripleCrossStrategy,
)
from selene_space_strategy.templates.presets.calendar_effect import (
    CalendarEffectStrategy,
)
from selene_space_strategy.templates.presets.tema_channel import TEMAChannelStrategy
from selene_space_strategy.templates.presets.keltner_v2 import KeltnerChannelV2Strategy
from selene_space_strategy.templates.presets.pyramid_ma import PyramidMAStrategy
from selene_space_strategy.templates.presets.ma_extended_runs import (
    MAExtendedRunsStrategy,
)
from selene_space_strategy.templates.presets.dca_grid import DCAGridStrategy
from selene_space_strategy.templates.presets.rsi_momentum_continuation import (
    RSIMomentumContinuationStrategy,
)
from selene_space_strategy.templates.presets.er_state_machine import (
    ERStateMachineStrategy,
)
from selene_space_strategy.templates.presets.chop_guard import ChopGuardStrategy
from selene_space_strategy.templates.presets.reversion_cost_guard import (
    ReversionCostGuardStrategy,
)
from selene_space_core.kline_paths import default_klines_db_path  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("llm_tuner")

# 默认配置
DEFAULT_INTERVALS = ["1d", "4h", "1h"]
DEFAULT_DB_PATH = default_klines_db_path()
DEFAULT_TRIALS = 30
DEFAULT_INITIAL_CAPITAL = 100000.0
DEFAULT_LEVERAGE = 1
DEFAULT_FEE_RATE = 0.0005

# 成本模型默认值(对齐 RealisticSlippage 基础 3bp,而非旧 1.5bp 标量):
# 调参 objective 必须建立在"真实成本世界"上,否则会对零成本假世界过拟合。
DEFAULT_SLIPPAGE_BPS = 3.0
DEFAULT_TAKER_FEE_RATE = 0.0005
DEFAULT_FUNDING_APY = 0.0  # 期货可显式开启(年化资金费率成本),现货保持 0

# 策略注册表: (策略类, 参数空间, 中文名称)
STRATEGY_REGISTRY: dict[
    str, tuple[type[BacktestStrategyTemplate], dict[str, Any], str]
] = {
    # === 布林带类 ===
    "bb_v3": (
        BBV3Strategy,
        {
            "bb_len": (5, 20),
            "buy_len": (20, 60),
            "buy_mult": (1.5, 4.0),
            "sell_len": (10, 40),
            "sell_mult": (1.5, 4.0),
            "buy_tp_pct": (0.05, 0.20),
            "buy_sl_pct": (0.03, 0.12),
        },
        "布林带升级版",
    ),
    "bb_atr_trend": (
        BBATRTrendStrategy,
        {
            "bb_len": (10, 30),
            "bb_mult": (1.5, 3.0),
            "atr_len": (7, 21),
            "rsi_len": (7, 21),
            "rsi_long_min": (20, 40),
            "rsi_long_max": (60, 80),
            "tp_pct": (0.03, 0.10),
            "sl_pct": (0.02, 0.06),
        },
        "布林ATR趋势",
    ),
    # === RSI类 ===
    "rsi_ma_delta": (
        RSIMADeltaStrategy,
        {
            "rsi_len": (7, 21),
            "ma_len1": (7, 21),
            "ma_len2": (3, 14),
            "tp_pct": (0.03, 0.10),
            "sl_pct": (0.02, 0.06),
        },
        "RSI偏离均线",
    ),
    # === DMI类 ===
    "dmi_delta": (
        DMIDeltaStrategy,
        {
            "dmi_len": (7, 21),
            "ma_len": (7, 21),
            "tp_pct": (0.03, 0.10),
            "sl_pct": (0.02, 0.06),
        },
        "DMI差值",
    ),
    # === 信号类 ===
    "p_signal": (
        PSignalStrategy,
        {
            "n_bars": (5, 15),
            "tp_pct": (0.03, 0.10),
            "sl_pct": (0.02, 0.06),
        },
        "P信号熵",
    ),
    # === 随机指标类 ===
    "stoch_supertrend": (
        STOCHSupertrendStrategy,
        {
            "k_len": (7, 21),
            "k_smooth_len": (5, 15),
            "length": (1, 5),
            "percent": (1.0, 4.0),
            "tp_pct": (0.03, 0.10),
            "sl_pct": (0.02, 0.06),
        },
        "随机超级趋势",
    ),
    # === 成交量类 ===
    "vw_ema_cross": (
        VWEMACrossoverStrategy,
        {
            "vw_period": (10, 30),
            "ema_len": (5, 15),
            "tp_pct": (0.03, 0.10),
            "sl_pct": (0.02, 0.06),
        },
        "量加权EMA交叉",
    ),
    "vwap_fibo_bands": (
        VWAPFiboBandsStrategy,
        {
            "fibo_inner": (1.382, 2.0),
            "fibo_outer": (2.0, 3.236),
            "min_bars": (2, 8),
            "tp_pct": (0.02, 0.06),
            "sl_pct": (0.03, 0.08),
        },
        "VWAP斐波那契通道",
    ),
    # === 均线类 ===
    "ema_triple_cross": (
        EMATripleCrossStrategy,
        {
            "short_period": (5, 15),
            "mid_period": (20, 40),
            "long_period": (100, 200),
            "atr_period": (7, 21),
            "stop_atr_mult": (1.5, 3.0),
            "tp_pct": (0.03, 0.10),
            "sl_pct": (0.01, 0.04),
        },
        "三均线交叉",
    ),
    "pyramid_ma": (
        PyramidMAStrategy,
        {
            "short_p": (3, 10),
            "mid_short_p": (7, 15),
            "mid_p": (15, 30),
            "long_p": (40, 80),
            "take_profit_pct": (0.02, 0.06),
            "stop_loss_pct": (0.05, 0.15),
            "max_positions": (3, 10),
        },
        "金字塔均线",
    ),
    "ma_extended_runs": (
        MAExtendedRunsStrategy,
        {
            "ma_len": (15, 40),
            "deviation": (5, 20),
            "hold_bars": (2, 8),
            "atr_len": (20, 60),
            "atr_mult": (2.0, 4.0),
            "stop_loss_pct": (0.05, 0.15),
            "take_profit_pct": (0.50, 1.50),
        },
        "均线连续偏离",
    ),
    # === DCA网格类 ===
    "dca_grid": (
        DCAGridStrategy,
        {
            "fast_ma": (5, 15),
            "slow_ma": (20, 50),
            "base_size": (1.0, 10.0),
            "max_trades": (5, 15),
            "grid_spacing": (0.002, 0.01),
            "grid_price_scale": (1.1, 1.5),
            "grid_volume_scale": (1.5, 2.5),
            "take_profit_pct": (0.01, 0.03),
            "stop_loss_pct": (0.03, 0.10),
        },
        "DCA网格",
    ),
    # === 日历效应类 ===
    "calendar_effect": (
        CalendarEffectStrategy,
        {
            "buy_day": (20, 28),
            "sell_day": (1, 10),
            "hold_max_days": (10, 20),
            "tp_pct": (0.05, 0.15),
            "sl_pct": (0.03, 0.08),
        },
        "日历效应",
    ),
    # === 通道类 ===
    "tema_channel": (
        TEMAChannelStrategy,
        {
            "length": (10, 30),
            "mult": (1.5, 3.0),
            "tp_pct": (0.03, 0.10),
            "sl_pct": (0.02, 0.06),
        },
        "TEMA波动率通道",
    ),
    "keltner_v2": (
        KeltnerChannelV2Strategy,
        {
            "ma_length": (10, 30),
            "atr_length": (10, 30),
            "mult": (1.5, 3.0),
            "adx_length": (7, 21),
            "adx_threshold": (15, 35),
            "stop_pct": (0.02, 0.08),
            "tp_pct": (0.05, 0.15),
        },
        "肯特纳通道增强版",
    ),
    # === P1 新增模板 (A1/A4/A3/A7) ===
    "rsi_momentum_continuation": (
        RSIMomentumContinuationStrategy,
        {
            "rsi_len": (7, 21),
            "continue_threshold": (60, 80),
            "exit_threshold": (40, 60),
            "tp_pct": (0.03, 0.10),
            "sl_pct": (0.02, 0.06),
        },
        "RSI动量延续",
    ),
    "er_state_machine": (
        ERStateMachineStrategy,
        {
            "er_period": (7, 21),
            "entry_threshold": (0.30, 0.70),
            "exit_threshold": (0.15, 0.45),
            "tp_pct": (0.03, 0.10),
            "sl_pct": (0.02, 0.06),
        },
        "ER效率比率状态机",
    ),
    "chop_guard": (
        ChopGuardStrategy,
        {
            "chop_period": (7, 21),
            "ma_len": (10, 30),
            "tp_pct": (0.03, 0.10),
            "sl_pct": (0.02, 0.06),
        },
        "CHOP门卫趋势",
    ),
    "reversion_cost_guard": (
        ReversionCostGuardStrategy,
        {
            "ma_len": (10, 30),
            "min_edge_mult": (1.0, 3.0),
            "tp_pct": (0.03, 0.10),
            "sl_pct": (0.02, 0.06),
        },
        "均值回归成本门卫",
    ),
}


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------


def load_kline_data(
    db_path: Path,
    interval: str,
    exchange: str = "binance",
    market_type: str = "futures",
    symbol: str = "BTC/USDT",
) -> list[KlineEvent]:
    """Load K-line data from local SQLite database."""
    db = KlineDB(db_path)

    # Get data
    df = db.get_klines(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        interval=interval,
    )

    if df.empty:
        logger.warning(
            f"No data found for {exchange}/{market_type}/{symbol}/{interval}"
        )
        return []

    # Convert DataFrame to KlineEvent list
    klines: list[KlineEvent] = []
    for _, row in df.iterrows():
        klines.append(
            KlineEvent(
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                timestamp=int(row["timestamp"]),
                interval=interval,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                closed=True,
            )
        )

    db.close()
    return klines


# ---------------------------------------------------------------------------
# Tuning Logic
# ---------------------------------------------------------------------------


def _compute_buy_hold(
    data: list[KlineEvent], periods_per_year: int
) -> dict[str, float]:
    """Inline buy-and-hold benchmark stats on the same klines (no trading cost).

    Annualization uses the same periods_per_year as DSR/haircut so the
    benchmark is directly comparable to the strategy's overfit metrics.
    """
    if len(data) < 2:
        return {
            "sharpe": 0.0,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "annual_vol": 0.0,
        }

    closes = np.asarray([k.close for k in data], dtype=float)
    returns = np.diff(closes) / closes[:-1]
    total_return = float(closes[-1] / closes[0] - 1.0)
    vol_std = float(np.std(returns))
    annual_vol = vol_std * math.sqrt(periods_per_year)
    sharpe = (
        (float(np.mean(returns)) * math.sqrt(periods_per_year) / vol_std)
        if vol_std > 0
        else 0.0
    )

    cum = closes / closes[0]
    max_drawdown = float(np.max(1.0 - cum / np.maximum.accumulate(cum)))

    return {
        "sharpe": sharpe,
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "annual_vol": annual_vol,
    }


# 回测结果只保留标量指标进 JSON；equity_curve（每 bar 一点）和 trades
# （每笔一记录）会把单条 record 膨胀到数 MB，且所有消费方（前端面板、
# research API、view 脚本）均不读取它们。
_BACKTEST_SCALAR_KEYS = (
    "total_return",
    "annual_return",
    "sharpe_ratio",
    "max_drawdown",
    "win_rate",
    "profit_factor",
    "total_trades",
    "calmar_ratio",
    "sortino_ratio",
    "expectancy",
)


def _slim_backtest_dict(result: Any) -> dict[str, float] | None:
    """Extract scalar metrics from a BacktestResult-like object (or None)."""
    if result is None:
        return None
    return {key: float(getattr(result, key)) for key in _BACKTEST_SCALAR_KEYS}


def _oos_gate(record: dict[str, Any], min_ratio: float, min_abs: float | None) -> bool:
    """防过拟合硬门: OOS 衰减超阈值 → True(踢出排名)。

    - ratio 门只在 IS(best_sharpe) > 0 时生效, IS<=0 退化按绝对门判(除法语义坑)
    - min_abs 为 None 时绝对门关闭; ratio<=0 时比值门关闭
    - oos_sharpe 为 None(数据过短等) → 不受门影响, 返回 False
    """
    oos = record.get("oos_sharpe")
    if oos is None:
        return False
    if min_abs is not None and oos < min_abs:
        return True
    is_sharpe = record.get("best_sharpe", 0.0) or 0.0
    if min_ratio > 0 and is_sharpe > 0 and oos / is_sharpe < min_ratio:
        return True
    return False


def _unique_tmp_path(output_file: Path) -> Path:
    """Per-PID tmp name so concurrent tuner runs never clobber each other."""
    return output_file.with_name(f"{output_file.name}.{os.getpid()}.tmp")


def tune_strategy(
    strategy_id: str,
    strategy_class: type[BacktestStrategyTemplate],
    param_space: dict[str, Any],
    data: list[KlineEvent],
    n_trials: int,
    interval: str,
    *,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    taker_fee_rate: float = DEFAULT_TAKER_FEE_RATE,
    funding_rate_apy: float = DEFAULT_FUNDING_APY,
    oos: bool = False,
    objective: str = "sharpe_ratio",
    n_seeds: int = 1,
    min_trades: int = 0,
    market_type: str = "futures",
    exchange: str = "binance",
) -> dict[str, Any] | None:
    """Run Bayesian optimization for a single strategy.

    When ``oos`` is True, the data is split 70/30 by time: the first 70% is
    used to tune (find best params), and the last 30% is held out to evaluate
    those best params — the resulting ``oos_sharpe`` measures true out-of-sample
    net performance instead of relying on in-sample Sharpe alone.
    """
    strategy_name = STRATEGY_REGISTRY[strategy_id][2]

    try:
        # OQ-3: tuner consumes the annualization table. Resolve the market
        # domain from the combo (equity symbols override the crypto default);
        # CRYPTO keeps the legacy 365-series numbers unchanged.
        domain = resolve_market_domain(market_type, data[0].symbol, exchange)
        if domain is MarketDomain.CRYPTO:
            periods_per_year = _periods_per_year_from_interval(interval)
        else:
            from selene_space_types.calendars import annualization_factor

            periods_per_year = annualization_factor(domain, interval)

        optimizer = StrategyOptimizer(
            strategy_class=strategy_class,
            objective_metric=objective,
            initial_capital=DEFAULT_INITIAL_CAPITAL,
            leverage=DEFAULT_LEVERAGE,
            fee_rate=DEFAULT_FEE_RATE,
            long_only=True,
            slippage_bps=slippage_bps,
            taker_fee_rate=taker_fee_rate,
            funding_rate_apy=funding_rate_apy,
            overfit_check=True,
            min_trades=min_trades,
            periods_per_year=periods_per_year,
        )

        tune_data = data
        if oos and len(data) >= 4:
            split = max(1, int(len(data) * 0.7))
            tune_data = data[:split]
            test_data = data[split:]

        result = optimizer.bayesian_optimize(
            data=tune_data,
            param_space=param_space,
            symbol="BTC/USDT",
            n_trials=n_trials,
            maximize=True,
            verbose=True,
            n_seeds=n_seeds,
        )

        buyhold = _compute_buy_hold(tune_data, periods_per_year)

        # 16 keys append-only: existing 8 + 8 new (aligned with OverfitReport fields)
        record: dict[str, Any] = {
            "strategy_id": strategy_id,
            "strategy_name": strategy_name,
            "interval": interval,
            "best_params": result.best_params,
            "best_sharpe": result.best_value,
            "metric_label": "净收益Sharpe",
            "cost_config": {
                "slippage_bps": slippage_bps,
                "taker_fee_rate": taker_fee_rate,
                "funding_rate_apy": funding_rate_apy,
            },
            "n_trials": result.n_trials,
            "optimization_time": result.optimization_time,
            "backtest_result": _slim_backtest_dict(result.best_result),
            "seed_std": result.seed_std,
            "all_rejected": result.all_rejected,
            "deflated_sharpe_ratio": None,
            "haircut_sharpe_annual": None,
            "pbo": None,
            "buy_hold_sharpe": buyhold["sharpe"],
            "buy_hold_return": buyhold["total_return"],
            "buy_hold_drawdown": buyhold["max_drawdown"],
            "buy_hold_vol": buyhold["annual_vol"],
        }
        if result.overfit_report:
            record["deflated_sharpe_ratio"] = result.overfit_report.get(
                "deflated_sharpe_ratio"
            )
            record["haircut_sharpe_annual"] = result.overfit_report.get(
                "haircut_sharpe_annual"
            )
            # None when fewer than two configs survived the gate — CSCV has
            # nothing to rank, and "not computed" must not read as "passed".
            record["pbo"] = result.overfit_report.get("pbo")

        if oos:
            record["oos_split"] = {"train": len(tune_data), "test": len(test_data)}
            if test_data:
                oos_strategy = optimizer._create_strategy(
                    result.best_params, symbol="BTC/USDT"
                )
                oos_result = oos_strategy.run_backtest(test_data)
                record["oos_sharpe"] = oos_result.sharpe_ratio
                record["oos_total_return"] = oos_result.total_return
                record["oos_trades"] = oos_result.total_trades
            else:
                record["oos_sharpe"] = None
                record["oos_total_return"] = None
                record["oos_trades"] = 0

        return record

    except Exception as e:
        logger.error(f"Failed to tune {strategy_id}: {e}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LLM-guided parameter tuning for strategy templates"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite database path",
    )
    parser.add_argument(
        "--intervals",
        nargs="+",
        default=DEFAULT_INTERVALS,
        choices=["1d", "4h", "1h", "30m", "5m", "1m"],
        help="K-line intervals to test",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=DEFAULT_TRIALS,
        help="Number of optimization trials per strategy",
    )
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=list(STRATEGY_REGISTRY.keys()),
        choices=list(STRATEGY_REGISTRY.keys()),
        help="Strategies to tune",
    )
    parser.add_argument(
        "--exchange",
        nargs="+",
        default=["binance"],
        choices=["binance", "okx", "yahoo"],
        help="Exchange(s) to use (yahoo = US stock line)",
    )
    parser.add_argument(
        "--market-type",
        nargs="+",
        default=["futures"],
        choices=["futures", "spot", "us_stock", "ashare"],
        help="Market type(s) to test",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BTC/USDT"],
        help="Trading pair symbols (e.g. BTC/USDT ETH/USDT SOL/USDT)",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="(Deprecated, use --symbols) Single trading pair symbol",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory for results",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="Tag for this run (used in output filename, e.g. 'A_btc_4h')",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=DEFAULT_SLIPPAGE_BPS,
        help=f"Per-side slippage in basis points (default {DEFAULT_SLIPPAGE_BPS}, aligned with RealisticSlippage base)",
    )
    parser.add_argument(
        "--taker-fee",
        type=float,
        default=DEFAULT_TAKER_FEE_RATE,
        help=f"Taker fee rate (default {DEFAULT_TAKER_FEE_RATE})",
    )
    parser.add_argument(
        "--funding-apy",
        type=float,
        default=DEFAULT_FUNDING_APY,
        help="Annualized funding-rate cost for futures (default 0.0; e.g. 0.05 = 5%% APY)",
    )
    parser.add_argument(
        "--oos",
        action="store_true",
        default=False,
        help="Walk-forward OOS validation: tune on first 70%% of data, evaluate best params on last 30%%",
    )
    parser.add_argument(
        "--objective",
        default="sharpe_ratio",
        choices=[
            "sharpe",
            "sharpe_ratio",
            "total_return",
            "annual_return",
            "profit_factor",
            "win_rate",
            "calmar_ratio",
            "sortino_ratio",
            "expectancy",
        ],
        help="Objective metric to optimize ('sharpe' is an alias for 'sharpe_ratio')",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        default=1,
        help="Number of independent studies (multi-seed variance; best_value=mean)",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=0,
        help="Minimum trade count; trials with fewer trades are pruned",
    )
    parser.add_argument(
        "--headline",
        default="dsr",
        choices=["dsr", "sharpe"],
        help="Headline metric shown per row in console output",
    )
    parser.add_argument(
        "--min-oos-ratio",
        type=float,
        default=0.0,
        help="OOS/IS Sharpe 最低比值; 低于则踢出排名(防过拟合硬门, 0=关)",
    )
    parser.add_argument(
        "--min-oos-sharpe",
        type=float,
        default=None,
        help="OOS净Sharpe 绝对值下限; 低于则踢出排名(None=关)",
    )
    return parser.parse_args()


def main() -> None:
    _fix_windows_console_encoding()
    args = parse_args()

    # --objective sharpe → canonical sharpe_ratio (matches --headline's shorthand)
    if args.objective == "sharpe":
        args.objective = "sharpe_ratio"

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Backward-compat: --symbol (deprecated) overrides --symbols if set
    if args.symbol is not None:
        args.symbols = [args.symbol]

    # Setup output directory
    output_dir = args.output or (PROJECT_ROOT / "data" / "tuning_results")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build market combinations: (exchange, market_type, interval, symbol)
    market_combos: list[tuple[str, str, str, str]] = []
    for exchange in args.exchange:
        for market_type in args.market_type:
            for symbol in args.symbols:
                for interval in args.intervals:
                    market_combos.append((exchange, market_type, symbol, interval))

    print("=" * 70)
    print("LLM Parameter Tuner — 策略参数优化")
    print("=" * 70)
    print(f"数据库:      {args.db}")
    print(f"交易所:      {args.exchange}")
    print(f"市场类型:    {args.market_type}")
    print(f"交易对:      {args.symbols}")
    print(f"周期:        {args.intervals}")
    print(f"策略数:      {len(args.strategies)}")
    print(f"市场组合:    {len(market_combos)}")
    print(f"总调参轮数:  {len(args.strategies) * len(market_combos)}")
    print(f"迭代次数:    {args.trials}")
    print(f"滑点(每边):  {args.slippage_bps} bp")
    print(f"Taker费率:   {args.taker_fee}")
    print(f"资金费率:    {args.funding_apy} APY")
    print(f"样本外验证:  {'开 (前70%调参→后30%评估)' if args.oos else '关'}")
    print(f"输出目录:    {output_dir}")
    print("=" * 70)

    # Results storage
    all_results: list[dict[str, Any]] = []
    skipped = 0
    failed = 0

    # Calculate total tasks for progress tracking
    total_tasks = len(market_combos) * len(args.strategies)

    # Incremental save: pre-create output file so partial results survive crashes
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag_suffix = f"_{args.tag}" if args.tag else ""
    output_file = output_dir / f"tuning_{timestamp}{tag_suffix}.json"

    def save_partial(final: bool = False) -> None:
        """Save current results incrementally. Overwrites same file."""
        tmp = _unique_tmp_path(output_file)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "schema_version": RESEARCH_ARTIFACT_SCHEMA_VERSION,
                    "role": "canonical",
                    "finding": {
                        "status": "inconclusive",
                        "why": None,
                        "negative_reason": None,
                        "regime_condition": None,
                    },
                    "lineage": {"parents": [], "protocol_hash": None},
                    "source": build_source_annotation(args.db, market_combos),
                    "config": {
                        "exchanges": args.exchange,
                        "market_types": args.market_type,
                        "symbols": args.symbols,
                        "intervals": args.intervals,
                        "strategies": args.strategies,
                        "n_trials": args.trials,
                        "oos": args.oos,
                        "min_oos_ratio": args.min_oos_ratio,
                        "min_oos_sharpe": args.min_oos_sharpe,
                        "tag": args.tag,
                    },
                    "summary": {
                        "total_combos": len(market_combos),
                        "successful": len(all_results),
                        "skipped": skipped,
                        "failed": failed,
                        "final": final,
                    },
                    "results": all_results,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        tmp.replace(output_file)

    # Initial empty save so file exists from the start
    save_partial()
    print(f"  增量保存到: {output_file}")

    # Run tuning for each market combination
    pbar = tqdm(
        total=total_tasks,
        desc="总进度",
        unit="task",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] {desc}",
    )

    for combo_idx, (exchange, market_type, symbol, interval) in enumerate(
        market_combos, 1
    ):
        combo_label = f"{exchange}/{market_type}/{symbol}/{interval}"

        # Load data for this combination
        klines = load_kline_data(
            args.db,
            interval,
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
        )

        if not klines:
            pbar.write(f"  跳过 {combo_label}: 无数据")
            skipped += len(args.strategies)
            pbar.update(len(args.strategies))
            continue

        # Tune each strategy
        for strat_idx, strategy_id in enumerate(args.strategies, 1):
            strategy_class, param_space, strategy_name = STRATEGY_REGISTRY[strategy_id]

            pbar.set_description_str(f"{combo_label} | {strategy_name}")
            result = tune_strategy(
                strategy_id=strategy_id,
                strategy_class=strategy_class,
                param_space=param_space,
                data=klines,
                n_trials=args.trials,
                interval=interval,
                slippage_bps=args.slippage_bps,
                taker_fee_rate=args.taker_fee,
                funding_rate_apy=args.funding_apy,
                oos=args.oos,
                objective=args.objective,
                n_seeds=args.seeds,
                min_trades=args.min_trades,
                market_type=market_type,
                exchange=exchange,
            )

            if result:
                result["exchange"] = exchange
                result["market_type"] = market_type
                result["symbol"] = symbol
                all_results.append(result)
                if result.get("all_rejected", False):
                    pbar.write(
                        f"  [{combo_label}] {strategy_name}: 全拒(min_trades 过滤)"
                    )
                elif args.headline == "dsr":
                    dsr = result.get("deflated_sharpe_ratio")
                    dsr_str = f"{dsr:.4f}" if dsr is not None else "N/A"
                    pbar.write(
                        f"  [{combo_label}] {strategy_name}: DSR={dsr_str} Sharpe={result['best_sharpe']:.4f}"
                    )
                else:
                    pbar.write(
                        f"  [{combo_label}] {strategy_name}: Sharpe={result['best_sharpe']:.4f}"
                    )
                if args.oos:
                    oos_s = result.get("oos_sharpe")
                    oos_str = (
                        f"OOS净Sharpe={oos_s:.4f}"
                        if oos_s is not None
                        else "OOS净Sharpe=NA"
                    )
                    pbar.write(f"    └ {oos_str}")
            else:
                failed += 1
                pbar.write(f"  [{combo_label}] {strategy_name}: 失败!")

            # Incremental save after each strategy
            save_partial()
            pbar.update(1)

    pbar.close()

    # Summary
    print("\n" + "=" * 70)
    print("调参结果汇总")
    print("=" * 70)

    if not all_results:
        print("无成功调参结果!")
        return

    # all-rejected records stay in the JSON output but never enter the ranking
    ranked = [r for r in all_results if not r.get("all_rejected", False)]
    if not ranked:
        print("全部记录被 min_trades 门槛过滤(all_rejected=True),无排名输出。")
        save_partial(final=True)
        return

    # OOS 防过拟合硬门: 被踢的留 JSON(审计)但不进排名,与 all_rejected 语义对齐
    kept: list[dict] = []
    for r in ranked:
        if args.oos and _oos_gate(r, args.min_oos_ratio, args.min_oos_sharpe):
            oos = r.get("oos_sharpe")
            ratio = (
                f"{oos / r['best_sharpe']:.2f}" if r.get("best_sharpe", 0) > 0 else "NA"
            )
            r["oos_rejected"] = True
            pbar.write(f"  └ OOS门拒绝(比值={ratio}, 阈值={args.min_oos_ratio})")
        else:
            kept.append(r)
    ranked = kept
    if not ranked:
        print(
            "全部记录被 OOS 防过拟合门踢出"
            f"(min_oos_ratio={args.min_oos_ratio}, min_oos_sharpe={args.min_oos_sharpe}),"
            "无排名输出(记录仍在 JSON 中审计)。"
        )
        save_partial(final=True)
        return

    # Sort by objective metric (best_sharpe holds the objective value)
    ranked.sort(key=lambda x: x["best_sharpe"], reverse=True)

    objective_label = {
        "sharpe_ratio": "Sharpe",
        "total_return": "Return",
        "annual_return": "AnnRet",
        "profit_factor": "ProfitF",
        "win_rate": "WinRate",
        "calmar_ratio": "Calmar",
        "sortino_ratio": "Sortino",
        "expectancy": "Expect",
    }.get(args.objective, args.objective)

    # Print table
    oos_col = f"{'OOS净Sharpe':>12}" if args.oos else ""
    header = f"\n{'排名':<4} {'策略':<16} {'交易对':<10} {'市场':<14} {'周期':<6} {objective_label:>10} {oos_col} {'迭代':>6} {'耗时':>8}"
    print(header)
    print("-" * (86 + (13 if args.oos else 0)))

    for rank, r in enumerate(ranked, 1):
        time_str = f"{r['optimization_time']:.1f}s"
        market_str = f"{r.get('exchange', '?')}/{r.get('market_type', '?')}"
        symbol_str = r.get("symbol", "?")
        if args.oos:
            oos_s = r.get("oos_sharpe")
            oos_val = f"{oos_s:>12.4f}" if oos_s is not None else f"{'NA':>12}"
            print(
                f"{rank:<4} {r['strategy_name']:<16} {symbol_str:<10} {market_str:<14} {r['interval']:<6} {r['best_sharpe']:>10.4f} {oos_val} {r['n_trials']:>6} {time_str:>8}"
            )
        else:
            print(
                f"{rank:<4} {r['strategy_name']:<16} {symbol_str:<10} {market_str:<14} {r['interval']:<6} {r['best_sharpe']:>10.4f} {r['n_trials']:>6} {time_str:>8}"
            )

    # Final save (mark as final)
    save_partial(final=True)

    print(f"\n结果已保存: {output_file}")
    print(f"成功: {len(all_results)}, 跳过: {skipped}, 失败: {failed}")
    print("=" * 70)


if __name__ == "__main__":
    main()

"""EnsembleOptimizer — Multi-strategy weight and parameter optimization.

Optimizes the combination of multiple strategies using Optuna:
- Phase 1: Weight-only optimization (strategy params fixed from prior tuning)
- Phase 2: Joint optimization (strategy params + weights together)

Uses a composite score to avoid overfitting to a single metric:
    score = w_sharpe * sharpe + w_calmar * calmar + w_mdd * (1 - |mdd|) + w_wr * win_rate

Usage:
    from selene_space_strategy.fusion.ensemble_optimizer import EnsembleOptimizer

    optimizer = EnsembleOptimizer(
        strategy_configs=[
            ("keltner_v2", KeltnerV2Strategy, best_params_keltner),
            ("dca_grid", DCAGridStrategy, best_params_dca),
        ],
    )
    result = optimizer.optimize_weights(data)
    print(result.best_weights)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

try:
    import optuna
    from optuna.samplers import TPESampler

    optuna.logging.set_verbosity(optuna.logging.WARNING)
except ImportError:
    optuna = None

from selene_space_strategy.template import BacktestResult, BacktestStrategyTemplate
from selene_space_types.market import KlineEvent


# ---------------------------------------------------------------------------
# Composite Score
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompositeScoreWeights:
    """Weights for the composite objective function.

    All weights should sum to 1.0 for interpretability.
    """

    sharpe: float = 0.4
    calmar: float = 0.3
    mdd: float = 0.2  # Uses (1 - |max_drawdown|)
    win_rate: float = 0.1


def composite_score(
    result: BacktestResult,
    weights: CompositeScoreWeights = CompositeScoreWeights(),
) -> float:
    """Calculate composite score from backtest result.

    Avoids overfitting to a single metric by blending Sharpe,
    Calmar, drawdown control, and win rate.

    Args:
        result: Backtest result with metrics.
        weights: Blending weights (should sum to 1.0).

    Returns:
        Composite score (higher is better).
    """
    sharpe_norm = min(max(result.sharpe_ratio, 0.0), 3.0) / 3.0
    calmar_norm = min(max(result.calmar_ratio, 0.0), 5.0) / 5.0
    mdd_norm = 1.0 - min(abs(result.max_drawdown), 1.0)
    wr_norm = result.win_rate

    return (
        weights.sharpe * sharpe_norm
        + weights.calmar * calmar_norm
        + weights.mdd * mdd_norm
        + weights.win_rate * wr_norm
    )


# ---------------------------------------------------------------------------
# Result Types
# ---------------------------------------------------------------------------


@dataclass
class EnsembleOptimizationResult:
    """Result from ensemble optimization."""

    phase: str = ""
    best_weights: dict[str, float] = field(default_factory=dict)
    best_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    best_score: float = 0.0
    best_backtest: BacktestResult | None = None
    n_trials: int = 0
    optimization_time: float = 0.0
    trial_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "phase": self.phase,
            "best_weights": self.best_weights,
            "best_params": self.best_params,
            "best_score": self.best_score,
            "n_trials": self.n_trials,
            "optimization_time": self.optimization_time,
            "trial_history": self.trial_history,
        }


# ---------------------------------------------------------------------------
# EnsembleOptimizer
# ---------------------------------------------------------------------------


class EnsembleOptimizer:
    """Optimizer for multi-strategy ensemble weight and parameter tuning.

    Two-phase optimization:
    - Phase 1 (weights only): Fix strategy params from prior tuning, optimize
      only the ensemble weights. Fast, small search space.
    - Phase 2 (joint): Optimize strategy params + weights together. Slower
      but finds the global optimum for the ensemble.

    Args:
        strategy_configs: List of (strategy_id, strategy_class, initial_params) tuples.
        score_weights: Composite score blending weights.
        initial_capital: Starting capital for backtests.
        leverage: Leverage multiplier.
        fee_rate: Trading fee rate.
        long_only: Long-only mode.
    """

    def __init__(
        self,
        strategy_configs: list[tuple[str, type[BacktestStrategyTemplate], dict[str, Any]]],
        score_weights: CompositeScoreWeights = CompositeScoreWeights(),
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        long_only: bool = True,
    ) -> None:
        if optuna is None:
            raise ImportError("optuna is required. Install with: pip install optuna>=3.4")

        self.strategy_configs = strategy_configs
        self.score_weights = score_weights
        self.initial_capital = initial_capital
        self.leverage = leverage
        self.fee_rate = fee_rate
        self.long_only = long_only

    def _create_strategy(
        self,
        strategy_class: type[BacktestStrategyTemplate],
        params: dict[str, Any],
        symbol: str = "BTC/USDT",
    ) -> BacktestStrategyTemplate:
        """Create strategy instance with parameters."""
        strategy = strategy_class(
            symbol=symbol,
            initial_capital=self.initial_capital,
            leverage=self.leverage,
            fee_rate=self.fee_rate,
            long_only=self.long_only,
        )
        strategy.params = params
        return strategy

    def _run_ensemble_backtest(
        self,
        data: list[KlineEvent],
        weights: dict[str, float],
        params_per_strategy: dict[str, dict[str, Any]],
        symbol: str = "BTC/USDT",
    ) -> BacktestResult:
        """Run ensemble backtest with given weights and parameters.

        Each strategy's get_signal() is called independently per bar to get
        raw BUY/SELL/HOLD. Signals are then fused via weighted voting, and
        the ensemble equity is tracked from fused decisions.

        Args:
            data: Historical kline events.
            weights: Strategy weights for fusion (keyed by strategy_id or with prefix).
            params_per_strategy: Parameters per strategy.
            symbol: Trading symbol.

        Returns:
            BacktestResult from the fused ensemble.
        """
        from selene_space_strategy.template import SignalType

        # Create fresh strategy instances
        strategies: list[tuple[str, BacktestStrategyTemplate]] = []
        for sid, strategy_class, _ in self.strategy_configs:
            params = params_per_strategy.get(sid, {})
            strategy = self._create_strategy(strategy_class, params, symbol)
            strategy._bars.clear()
            strategies.append((sid, strategy))

        # Bar-by-bar ensemble backtest
        capital = self.initial_capital
        position = 0.0
        entry_price = 0.0
        equity_curve: list[float] = [capital]
        trades: list[dict[str, Any]] = []

        for event in data:
            if not event.closed:
                continue

            # Feed bar to each strategy and collect raw signals via get_signal()
            buy_weight = 0.0
            sell_weight = 0.0
            for sid, strategy in strategies:
                # Manually append bar (same as on_kline does)
                strategy._bars.append({
                    "timestamp": event.timestamp,
                    "open": event.open,
                    "high": event.high,
                    "low": event.low,
                    "close": event.close,
                    "volume": event.volume,
                })
                if len(strategy._bars) > strategy.max_bars:
                    strategy._bars = strategy._bars[-strategy.max_bars:]

                if len(strategy._bars) < 2:
                    continue

                # Call get_signal directly for raw signal (bypasses position management)
                df = pd.DataFrame(strategy._bars)
                try:
                    sig = strategy.get_signal(df)
                except Exception:
                    continue

                w = weights.get(sid, weights.get(f"ensemble-opt-{sid}", 1.0))
                if sig == SignalType.BUY:
                    buy_weight += w
                elif sig in (SignalType.SELL, SignalType.CLOSE_LONG):
                    sell_weight += w

            # Weighted fusion
            total_weight = buy_weight + sell_weight
            fused_side = None
            if total_weight > 0:
                if buy_weight > sell_weight and buy_weight > total_weight / 2:
                    fused_side = "BUY"
                elif sell_weight > buy_weight and sell_weight > total_weight / 2:
                    fused_side = "SELL"

            price = event.close

            if fused_side == "BUY" and position <= 0:
                if position < 0:
                    pnl = (entry_price - price) * abs(position) - abs(position) * price * self.fee_rate
                    capital += pnl
                    trades.append({"side": "CLOSE_SHORT", "price": price, "pnl": pnl})
                size = (capital * self.leverage) / price if price > 0 else 0
                position = size
                entry_price = price
                capital -= size * price * self.fee_rate
                trades.append({"side": "BUY", "price": price, "size": size})

            elif fused_side == "SELL" and position >= 0:
                if position > 0:
                    pnl = (price - entry_price) * position - position * price * self.fee_rate
                    capital += pnl
                    trades.append({"side": "CLOSE_LONG", "price": price, "pnl": pnl})
                if not self.long_only:
                    size = (capital * self.leverage) / price if price > 0 else 0
                    position = -size
                    entry_price = price
                    capital -= size * price * self.fee_rate
                    trades.append({"side": "SELL", "price": price, "size": size})
                else:
                    position = 0.0

            # Mark-to-market equity
            if position > 0:
                mtm = capital + position * (event.close - entry_price)
            elif position < 0:
                mtm = capital + abs(position) * (entry_price - event.close)
            else:
                mtm = capital
            equity_curve.append(max(mtm, 0))

        return self._compute_metrics(equity_curve, trades)

    def _compute_metrics(
        self,
        equity_curve: list[float],
        trades: list[dict[str, Any]],
    ) -> BacktestResult:
        """Compute backtest metrics from equity curve and trades."""
        if len(equity_curve) < 2:
            return BacktestResult()

        equity = np.array(equity_curve)
        total_return = (equity[-1] - equity[0]) / equity[0] if equity[0] > 0 else 0.0

        # Sharpe
        returns = np.diff(equity) / equity[:-1]
        returns = returns[equity[:-1] > 0]
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(252))
        else:
            sharpe = 0.0

        # Max drawdown
        peak = np.maximum.accumulate(equity)
        dd = (equity - peak) / peak
        max_dd = float(np.min(dd)) if len(dd) > 0 else 0.0

        # Win rate
        pnls = [t["pnl"] for t in trades if "pnl" in t]
        win_rate = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0.0

        # Profit factor
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

        # Calmar
        annual_return = total_return  # Simplified; would need actual time basis
        calmar = abs(annual_return / max_dd) if max_dd != 0 else 0.0

        return BacktestResult(
            equity_curve=list(equity_curve),
            trades=trades,
            total_return=float(total_return),
            annual_return=float(annual_return),
            sharpe_ratio=float(sharpe),
            max_drawdown=float(max_dd),
            win_rate=float(win_rate),
            profit_factor=float(profit_factor),
            total_trades=len([t for t in trades if t.get("side") in ("BUY", "SELL")]),
            calmar_ratio=float(calmar),
        )

    # ------------------------------------------------------------------
    # Phase 1: Weight-Only Optimization
    # ------------------------------------------------------------------

    def optimize_weights(
        self,
        data: list[KlineEvent],
        n_trials: int = 50,
        symbol: str = "BTC/USDT",
        timeout: int | None = None,
        verbose: bool = False,
    ) -> EnsembleOptimizationResult:
        """Phase 1: Optimize ensemble weights with fixed strategy params.

        Strategy parameters are taken from the initial_params provided
        at construction. Only the ensemble fusion weights are optimized.

        Args:
            data: Historical kline events.
            n_trials: Number of Optuna trials.
            symbol: Trading symbol.
            timeout: Optional timeout in seconds.
            verbose: Print progress.

        Returns:
            EnsembleOptimizationResult with best weights.
        """
        start_time = time.time()

        strategy_ids = [sid for sid, _, _ in self.strategy_configs]
        fixed_params = {sid: params for sid, _, params in self.strategy_configs}

        study = optuna.create_study(
            direction="maximize",
            sampler=TPESampler(),
        )

        def objective(trial: optuna.Trial) -> float:
            # Sample weight for each strategy in [0.1, 3.0]
            weights = {}
            for sid in strategy_ids:
                weights[f"ensemble-opt-{sid}"] = trial.suggest_float(
                    f"w_{sid}", 0.1, 3.0,
                )

            result = self._run_ensemble_backtest(data, weights, fixed_params, symbol)
            return composite_score(result, self.score_weights)

        study.optimize(objective, n_trials=n_trials, timeout=timeout, show_progress_bar=verbose, catch=(Exception,))

        # Guard: no completed trials
        completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if not completed:
            clean_weights = {sid: 1.0 for sid in strategy_ids}
            best_result = self._run_ensemble_backtest(
                data, {f"ensemble-opt-{sid}": 1.0 for sid in strategy_ids}, fixed_params, symbol,
            )
            return EnsembleOptimizationResult(
                phase="weight_only",
                best_weights=clean_weights,
                best_params=fixed_params,
                best_score=composite_score(best_result, self.score_weights),
                best_backtest=best_result,
                n_trials=len(study.trials),
                optimization_time=time.time() - start_time,
                trial_history=[],
            )

        # Extract best
        best_weights = {
            f"ensemble-opt-{sid}": study.best_params[f"w_{sid}"]
            for sid in strategy_ids
        }
        # Also store without prefix for convenience
        clean_weights = {sid: study.best_params[f"w_{sid}"] for sid in strategy_ids}

        best_result = self._run_ensemble_backtest(
            data, best_weights, fixed_params, symbol,
        )

        trial_history = [
            {
                "trial_id": t.number,
                "weights": {sid: t.params[f"w_{sid}"] for sid in strategy_ids},
                "score": t.value,
            }
            for t in study.trials
        ]

        return EnsembleOptimizationResult(
            phase="weight_only",
            best_weights=clean_weights,
            best_params=fixed_params,
            best_score=study.best_value,
            best_backtest=best_result,
            n_trials=len(study.trials),
            optimization_time=time.time() - start_time,
            trial_history=trial_history,
        )

    # ------------------------------------------------------------------
    # Phase 2: Joint Optimization (weights + params)
    # ------------------------------------------------------------------

    def optimize_joint(
        self,
        data: list[KlineEvent],
        param_spaces: dict[str, dict[str, tuple[Any, Any]]],
        n_trials: int = 100,
        symbol: str = "BTC/USDT",
        timeout: int | None = None,
        verbose: bool = False,
    ) -> EnsembleOptimizationResult:
        """Phase 2: Jointly optimize ensemble weights and strategy parameters.

        Larger search space than Phase 1 but finds the global optimum
        for the strategy combination.

        Args:
            data: Historical kline events.
            param_spaces: Per-strategy parameter spaces.
                Example: {"keltner_v2": {"ma_length": (5, 30), "mult": (1.5, 4.0)}}
            n_trials: Number of Optuna trials.
            symbol: Trading symbol.
            timeout: Optional timeout in seconds.
            verbose: Print progress.

        Returns:
            EnsembleOptimizationResult with best weights and params.
        """
        start_time = time.time()

        strategy_ids = [sid for sid, _, _ in self.strategy_configs]

        study = optuna.create_study(
            direction="maximize",
            sampler=TPESampler(),
        )

        def objective(trial: optuna.Trial) -> float:
            # Sample weights
            weights = {}
            for sid in strategy_ids:
                weights[f"ensemble-opt-{sid}"] = trial.suggest_float(
                    f"w_{sid}", 0.1, 3.0,
                )

            # Sample strategy params
            params_per_strategy: dict[str, dict[str, Any]] = {}
            for sid in strategy_ids:
                params_per_strategy[sid] = {}
                if sid in param_spaces:
                    for pname, prange in param_spaces[sid].items():
                        low, high = prange
                        if isinstance(low, int) and isinstance(high, int):
                            params_per_strategy[sid][pname] = trial.suggest_int(
                                f"{sid}__{pname}", low, high,
                            )
                        else:
                            params_per_strategy[sid][pname] = trial.suggest_float(
                                f"{sid}__{pname}", float(low), float(high),
                            )
                else:
                    # Use fixed initial params if no search space provided
                    for _, _, init_params in self.strategy_configs:
                        if _ == sid:
                            params_per_strategy[sid] = init_params.copy()
                            break

            result = self._run_ensemble_backtest(data, weights, params_per_strategy, symbol)
            return composite_score(result, self.score_weights)

        study.optimize(objective, n_trials=n_trials, timeout=timeout, show_progress_bar=verbose, catch=(Exception,))

        # Guard: no completed trials
        completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if not completed:
            clean_weights = {sid: 1.0 for sid in strategy_ids}
            best_params = {sid: params.copy() for sid, _, params in self.strategy_configs}
            best_result = self._run_ensemble_backtest(
                data, {f"ensemble-opt-{sid}": 1.0 for sid in strategy_ids}, best_params, symbol,
            )
            return EnsembleOptimizationResult(
                phase="joint",
                best_weights=clean_weights,
                best_params=best_params,
                best_score=composite_score(best_result, self.score_weights),
                best_backtest=best_result,
                n_trials=len(study.trials),
                optimization_time=time.time() - start_time,
                trial_history=[],
            )

        # Extract best weights and params
        best_weights = {}
        best_params: dict[str, dict[str, Any]] = {}

        for sid in strategy_ids:
            best_weights[f"ensemble-opt-{sid}"] = study.best_params[f"w_{sid}"]
            best_params[sid] = {}

            if sid in param_spaces:
                for pname in param_spaces[sid]:
                    best_params[sid][pname] = study.best_params[f"{sid}__{pname}"]
            else:
                for _, _, init_params in self.strategy_configs:
                    if _ == sid:
                        best_params[sid] = init_params.copy()
                        break

        clean_weights = {sid: study.best_params[f"w_{sid}"] for sid in strategy_ids}

        best_result = self._run_ensemble_backtest(
            data,
            {f"ensemble-opt-{sid}": w for sid, w in clean_weights.items()},
            best_params,
            symbol,
        )

        trial_history = [
            {
                "trial_id": t.number,
                "score": t.value,
            }
            for t in study.trials
        ]

        return EnsembleOptimizationResult(
            phase="joint",
            best_weights=clean_weights,
            best_params=best_params,
            best_score=study.best_value,
            best_backtest=best_result,
            n_trials=len(study.trials),
            optimization_time=time.time() - start_time,
            trial_history=trial_history,
        )

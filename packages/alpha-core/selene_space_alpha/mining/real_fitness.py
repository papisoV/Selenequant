"""Real-backtest fitness engine for the formula-policy network.

Replaces MemeBacktest with actual BacktestStrategyTemplate backtesting.
Each formula is evaluated by running a full backtest on real K-line data
and using Sharpe ratio as the fitness score.

This makes REINFORCE directly optimize real trading metrics instead of
the DEX-oriented MemeBacktest fitness.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from selene_space_alpha.strategy_bridge import AlphaFormulaStrategy
from selene_space_core.kline_paths import default_klines_db_path
from selene_space_alpha.vocab import FormulaVocab

logger = logging.getLogger(__name__)


def _load_kline_events(db_path: str, symbol: str, exchange: str, interval: str):
    """Load kline events from SQLite for backtesting."""
    import sqlite3
    from selene_space_types.market import KlineEvent

    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange=? AND interval=? ORDER BY timestamp ASC",
        conn,
        params=(symbol, exchange, interval),
    )
    conn.close()

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    events = []
    for _, row in df.iterrows():
        events.append(
            KlineEvent(
                exchange=exchange,
                symbol=symbol,
                interval=interval,
                timestamp=int(row["timestamp"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                closed=True,
            )
        )
    return events


class RealBacktestFitness:
    """Evaluate formulas using real BacktestStrategyTemplate backtests.

    Runs backtests on multiple symbols/intervals and returns a composite
    fitness score based on Sharpe ratio, win rate, and drawdown.

    When ``overfit_n_trials`` is set, tracks trial Sharpes across the
    REINFORCE run and applies a DSR/PBO-based penalty to formulas that
    are likely overfit.
    """

    def __init__(
        self,
        db_path: str | None = None,
        symbols: list[str] | None = None,
        intervals: list[str] | None = None,
        exchange: str = "binance",
        initial_capital: float = 10000.0,
        fee_rate: float = 0.0004,
        long_only: bool = True,
        max_bars: int = 500,
        cache_size: int = 2048,
        overfit_n_trials: int | None = None,
        overfit_check_interval: int = 50,
        compute_ic: bool = False,
    ):
        if symbols is None:
            symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        if intervals is None:
            intervals = ["4h"]

        if db_path is None:
            db_path = str(default_klines_db_path())
        self.db_path = db_path
        self.symbols = symbols
        self.intervals = intervals
        self.exchange = exchange
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.long_only = long_only
        self.max_bars = max_bars

        # Overfit detection
        self.overfit_n_trials = overfit_n_trials
        self.overfit_check_interval = overfit_check_interval
        self._trial_sharpes: list[float] = []
        self._eval_count: int = 0

        # IC bonus
        self.compute_ic = compute_ic

        # Pre-load all kline events
        self._events_cache: dict[str, list] = {}
        for sym in symbols:
            for iv in intervals:
                key = f"{sym}:{iv}"
                try:
                    self._events_cache[key] = _load_kline_events(
                        db_path, sym, exchange, iv
                    )
                    logger.info(
                        "Loaded %d events for %s", len(self._events_cache[key]), key
                    )
                except Exception as e:
                    logger.warning("Failed to load %s: %s", key, e)

        # Formula result cache
        self._cache: dict[str, float] = {}
        self._cache_size = cache_size

    def evaluate(
        self,
        formula_tokens: list[int],
        vocab: FormulaVocab,
    ) -> float:
        """Evaluate a formula by running real backtests.

        Returns a fitness score combining:
        - Sharpe ratio (primary)
        - Win rate bonus
        - Drawdown penalty
        - Activity penalty (too few or too many trades)
        """
        cache_key = str(formula_tokens)
        if cache_key in self._cache:
            return self._cache[cache_key]

        from selene_space_strategy.template import BacktestStrategyTemplate, SignalType

        class AlphaBTStrategy(BacktestStrategyTemplate):
            def __init__(self, ft, v, **kw):
                super().__init__(**kw)
                self._alpha = AlphaFormulaStrategy(formula_tokens=ft, vocab=v)

            def get_signal(self, data):
                sig = self._alpha.get_signal(data)
                if sig == "BUY":
                    return SignalType.BUY
                elif sig == "SELL":
                    return SignalType.SELL
                return SignalType.HOLD

        sharpes = []
        returns = []
        max_dds = []
        win_rates = []
        trade_counts = []

        for key, events in self._events_cache.items():
            try:
                strategy = AlphaBTStrategy(
                    formula_tokens,
                    vocab,
                    initial_capital=self.initial_capital,
                    fee_rate=self.fee_rate,
                    long_only=self.long_only,
                    max_bars=self.max_bars,
                )
                result = strategy.run_backtest(events)

                if result.total_trades < 3:
                    # Too few trades — penalty
                    sharpes.append(-2.0)
                    returns.append(-1.0)
                    max_dds.append(0.0)
                    win_rates.append(0.0)
                    trade_counts.append(0)
                    continue

                sharpes.append(result.sharpe_ratio)
                returns.append(result.total_return)
                max_dds.append(result.max_drawdown)
                win_rates.append(result.win_rate)
                trade_counts.append(result.total_trades)

            except Exception:
                sharpes.append(-3.0)
                returns.append(-1.0)

        if not sharpes:
            return -5.0

        # Composite fitness: average Sharpe + bonuses/penalties
        avg_sharpe = float(np.mean(sharpes))
        avg_dd = np.mean(max_dds) if max_dds else 0.0
        avg_wr = np.mean(win_rates) if win_rates else 0.0
        avg_trades = np.mean(trade_counts) if trade_counts else 0

        # Trade count penalty: too many = overtrading, too few = inactive
        n_bars = len(next(iter(self._events_cache.values()), []))
        if n_bars > 0:
            trade_ratio = avg_trades / n_bars
            # Ideal: 5-15% of bars have trades
            if trade_ratio > 0.3:
                overtrade_penalty = (trade_ratio - 0.15) * 10
            elif trade_ratio < 0.02:
                overtrade_penalty = (0.02 - trade_ratio) * 50
            else:
                overtrade_penalty = 0.0
        else:
            overtrade_penalty = 0.0

        # Drawdown penalty: cap at 30% max acceptable
        dd_penalty = max(0, -avg_dd - 0.30) * 5

        # Win rate bonus
        wr_bonus = (avg_wr - 0.40) * 2 if avg_wr > 0.40 else 0

        fitness = float(avg_sharpe - overtrade_penalty - dd_penalty + wr_bonus)

        # IC bonus: when multi-symbol data available, add cross-sectional IC reward
        if self.compute_ic and len(self._events_cache) > 1:
            try:
                from selene_space_backtest.metrics import compute_ic_summary

                # Use per-symbol returns as factor values vs forward returns
                symbol_returns = np.array(returns)
                if len(symbol_returns) > 1 and np.std(symbol_returns) > 1e-10:
                    # Simple proxy: rank symbols by return as factor signal
                    factor_values = symbol_returns.reshape(-1, 1)
                    forward_returns = symbol_returns.reshape(-1, 1)
                    ic_summary = compute_ic_summary(factor_values, forward_returns)
                    ic_bonus = max(0.0, ic_summary.icir) * 0.5
                    fitness += ic_bonus
            except Exception:
                pass  # IC bonus is optional, don't fail on it

        # Overfit detection: track trial Sharpes and penalize if overfit
        if self.overfit_n_trials is not None:
            self._trial_sharpes.append(avg_sharpe)
            self._eval_count += 1
            if (
                self._eval_count % self.overfit_check_interval == 0
                and len(self._trial_sharpes) >= 10
            ):
                overfit_report = self._check_overfit(avg_sharpe)
                if overfit_report is not None and not overfit_report.passed:
                    overfit_penalty = (1.0 - overfit_report.deflated_sharpe_ratio) * 2.0
                    fitness -= overfit_penalty
                    logger.warning(
                        "Overfit detected for formula %s: DSR=%.3f, penalty=%.3f",
                        cache_key[:20],
                        overfit_report.deflated_sharpe_ratio,
                        overfit_penalty,
                    )

        # Cache
        self._cache[cache_key] = fitness
        if len(self._cache) > self._cache_size:
            # Evict oldest half
            keys = list(self._cache.keys())
            for k in keys[: len(keys) // 2]:
                del self._cache[k]

        return fitness

    def _check_overfit(self, current_sharpe: float):
        """Run DSR/PBO check on the current best formula's Sharpe.

        Uses accumulated trial Sharpes to estimate cross-trial variance.
        Returns an OverfitReport or None if insufficient data.
        """
        from selene_space_backtest.overfit import build_overfit_report

        if len(self._trial_sharpes) < 10:
            return None

        # Build synthetic returns from the current Sharpe for DSR check.
        # We use the trial Sharpes to estimate the cross-trial variance.
        n_obs = max(100, len(next(iter(self._events_cache.values()), [])))
        rng = np.random.default_rng(42)
        # Reconstruct approximate per-bar returns from Sharpe
        if abs(current_sharpe) > 1e-6:
            sr_per_period = current_sharpe / np.sqrt(252)
            sigma = 0.01  # typical daily vol
            mu = sr_per_period * sigma
            synthetic_returns = rng.normal(mu, sigma, size=n_obs)
        else:
            synthetic_returns = rng.normal(0, 0.01, size=n_obs)

        try:
            report = build_overfit_report(
                selected_returns=synthetic_returns,
                n_trials=self.overfit_n_trials or len(self._trial_sharpes),
                periods_per_year=252,
            )
            return report
        except Exception as e:
            logger.debug("Overfit check failed: %s", e)
            return None

"""StrategyOptimizer — parameter search over BacktestStrategyTemplate strategies.

Three search modes share one backtest harness:
- grid_search(): exhaustive sweep of a Cartesian product
- bayesian_optimize(): Optuna-driven search (TPE / random / CmaEs samplers)
- walk_forward_validate(): per-fold IS tuning with OOS honesty check

Usage:
    optimizer = StrategyOptimizer(MyStrategy)
    outcome = optimizer.bayesian_optimize(
        data=kline_events,
        param_space={'rsi_period': (5, 30), 'oversold': [20, 30, 40]},
        n_trials=100,
    )
    print(outcome.best_params, outcome.best_value)
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from itertools import product as _cartesian_product
from typing import Any, Mapping

import numpy as np

from selene_space_strategy.template import BacktestResult, BacktestStrategyTemplate
from selene_space_types.calendars import annualization_factor as _domain_annualization
from selene_space_types.market import KlineEvent
from selene_space_types.routing import MarketDomain

try:  # optuna powers the Bayesian search path; grid/walk-forward degrade without it
    import optuna
    from optuna.samplers import TPESampler

    optuna.logging.set_verbosity(optuna.logging.WARNING)
except ImportError:  # pragma: no cover
    optuna = None  # type: ignore[assignment]


# Bars contained in one trading day per supported kline granularity.
_INTERVAL_BARS_PER_DAY: dict[str, int] = {
    "1d": 1,
    "4h": 6,
    "1h": 24,
    "30m": 48,
    "5m": 288,
    "1m": 1440,
}

# Annual bar counts, derived so the table can never drift from the day map.
INTERVAL_PERIODS_PER_YEAR: dict[str, int] = {
    interval: bars * 365 for interval, bars in _INTERVAL_BARS_PER_DAY.items()
}

# Upper bound on trial equity series retained for PBO's CSCV matrix. The
# statistic is computed once at the end; capping keeps a runaway sweep
# (hundreds of trials over thousands of bars) from pinning tens of MB.
# CSCV only needs enough columns to rank competing configurations, not all.
_PBO_MAX_TRIALS = 128


def _period_returns(
    equity_curve: list[float],
) -> "np.ndarray[Any, np.dtype[np.float64]]":
    """Per-period simple returns of an equity curve (empty when too short).

    Zero/negative equity points are floored to 1.0 for the denominator so a
    blown-up account yields a large negative return instead of inf/nan.
    """
    equity = np.asarray(equity_curve, dtype=float)
    if equity.size < 3:
        return np.array([], dtype=float)
    prev = equity[:-1]
    safe = np.where(prev > 0, prev, 1.0)
    returns: np.ndarray[Any, np.dtype[np.float64]] = np.diff(equity) / safe
    return returns


def _stack_trial_returns(trial_returns: list[np.ndarray]) -> np.ndarray | None:
    """Stack per-trial return series into the (T x N) matrix PBO expects.

    Returns ``None`` when CSCV cannot be run: fewer than two usable trials
    (nothing to rank against each other) or fewer than two periods. Series
    are truncated to a common length; every trial replays the same bars, so
    this is defensive rather than expected to bite.
    """
    usable = [r for r in trial_returns if r.size >= 2]
    if len(usable) < 2:
        return None
    length = min(r.size for r in usable)
    if length < 2:
        return None
    return np.column_stack([r[:length] for r in usable])


def _periods_per_year_from_interval(interval: str) -> int:
    """Translate an interval label into its annual bar count.

    Annualization factors elsewhere (overfit/report.py) multiply by the square
    root of this number; an unmapped interval is an error, never a silent
    fallback.
    """
    try:
        return INTERVAL_PERIODS_PER_YEAR[interval]
    except KeyError:
        known = ", ".join(sorted(INTERVAL_PERIODS_PER_YEAR))
        raise ValueError(
            f"Unknown interval {interval!r}. Legal intervals: {known}"
        ) from None


@dataclass
class OptimizationResult:
    """Best parameters found plus the evidence trail of every trial."""

    # Winning configuration and its score (score is a mean across seeds when
    # n_seeds > 1; best_params belongs to the strongest single replication).
    best_params: dict[str, Any] = field(default_factory=dict)
    best_value: float = 0.0
    best_result: BacktestResult | None = None
    # Effort accounting.
    n_trials: int = 0
    optimization_time: float = 0.0
    trial_history: list[dict[str, Any]] = field(default_factory=list)
    # DSR/PBO diagnostics (populated only with overfit_check=True).
    overfit_report: dict[str, Any] | None = None
    # Per-replication spread of the objective; empty when n_seeds == 1 or
    # every replication was pruned by the min_trades gate.
    seed_values: list[float] = field(default_factory=list)
    seed_std: float = 0.0
    all_rejected: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready projection (drops non-serializable ``best_result``)."""
        payload: dict[str, Any] = {"best_params": self.best_params}
        payload["best_value"] = self.best_value
        payload["n_trials"] = self.n_trials
        payload["optimization_time"] = self.optimization_time
        payload["trial_history"] = self.trial_history
        payload["seed_values"] = self.seed_values
        payload["seed_std"] = self.seed_std
        payload["all_rejected"] = self.all_rejected
        return payload


@dataclass
class WalkForwardResult:
    """Aggregated in-sample vs out-of-sample statistics across folds."""

    folds: list[dict[str, Any]] = field(default_factory=list)
    avg_is_sharpe: float = 0.0
    avg_oos_sharpe: float = 0.0
    avg_is_return: float = 0.0
    avg_oos_return: float = 0.0
    stability_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready projection."""
        return {
            "folds": self.folds,
            "avg_is_sharpe": self.avg_is_sharpe,
            "avg_oos_sharpe": self.avg_oos_sharpe,
            "avg_is_return": self.avg_is_return,
            "avg_oos_return": self.avg_oos_return,
            "stability_score": self.stability_score,
        }


class StrategyOptimizer:
    """Drives repeated backtests to locate strong strategy parameters.

    The optimizer instantiates fresh strategies per trial through
    :meth:`_create_strategy`, scores them via :meth:`_get_metric_value`, and
    supports multi-seed studies whose aggregate statistics expose how much of
    the result is sampler luck.
    """

    def __init__(
        self,
        strategy_class: type[BacktestStrategyTemplate],
        objective_metric: str = "sharpe_ratio",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        long_only: bool = True,
        overfit_check: bool = False,
        # Phase 1: trading cost realism (V4-aligned defaults)
        slippage_bps: float = 1.5,
        taker_fee_rate: float | None = None,
        funding_rate_apy: float = 0.0,
        min_trades: int = 0,
        periods_per_year: int | None = None,
        market_domain: MarketDomain | None = None,
    ) -> None:
        """Store search configuration; Optuna must be importable.

        Args:
            min_trades: Trials producing fewer fills raise optuna.TrialPruned;
                0 turns the gate off.
            periods_per_year: Bars/year for annualization-aware reports; None
                defers to bayesian_optimize's argument, then the domain
                calendar (CRYPTO 365 series by default — OQ-2, 2026-09-28).
            market_domain: Market domain for annualization. None keeps the
                legacy crypto default path untouched.
        """
        if optuna is None:
            raise ImportError(
                "optuna is required for StrategyOptimizer. Install with: pip install optuna>=3.4"
            )

        self.strategy_class = strategy_class
        self.objective_metric = objective_metric
        self.initial_capital = initial_capital
        self.leverage = leverage
        self.fee_rate = fee_rate
        self.long_only = long_only
        self.overfit_check = overfit_check
        self.slippage_bps = slippage_bps
        self.taker_fee_rate = taker_fee_rate
        self.funding_rate_apy = funding_rate_apy
        self.min_trades = min_trades
        self.periods_per_year = periods_per_year
        self.market_domain = market_domain

    # Account-construction knobs the optimizer always sets itself; sampled
    # params must never collide with these.
    _ACCOUNT_KEYS = frozenset(
        {
            "symbol",
            "initial_capital",
            "leverage",
            "fee_rate",
            "max_bars",
            "long_only",
            "slippage_bps",
            "taker_fee_rate",
            "funding_rate_apy",
        }
    )

    def _create_strategy(
        self, params: dict[str, Any], symbol: str = "BTC/USDT"
    ) -> BacktestStrategyTemplate:
        """Instantiate the strategy class under this optimizer's account config.

        Sampled params go in as constructor kwargs (filtered to the class's
        named signature) — template strategies keep typed attrs like
        ``self.bb_len`` and never read ``self.params``, so a bare attribute
        assignment would silently leave every trial running on defaults.
        The ``instance.params`` assignment stays for strategies that opt in
        via the ``self.params``-dict convention.
        """
        if isinstance(params, dict) and params:
            try:
                sig_params: Mapping[str, object] = inspect.signature(
                    self.strategy_class.__init__
                ).parameters
            except (TypeError, ValueError):
                sig_params = {}
            sampled = {
                key: value
                for key, value in params.items()
                if key not in self._ACCOUNT_KEYS and key in sig_params
            }
        else:
            sampled = {}
        instance = self.strategy_class(
            slippage_bps=self.slippage_bps,
            taker_fee_rate=self.taker_fee_rate,
            funding_rate_apy=self.funding_rate_apy,
            symbol=symbol,
            initial_capital=self.initial_capital,
            leverage=self.leverage,
            fee_rate=self.fee_rate,
            long_only=self.long_only,
            **sampled,
        )
        instance.params = params  # type: ignore[attr-defined]  # dynamic attrs by design
        return instance

    def _get_metric_value(self, result: BacktestResult) -> float:
        """Read the configured objective off a finished BacktestResult."""
        table: dict[str, float] = {
            "sharpe_ratio": result.sharpe_ratio,
            "total_return": result.total_return,
            "annual_return": result.annual_return,
            "profit_factor": result.profit_factor,
            "win_rate": result.win_rate,
            "calmar_ratio": result.calmar_ratio,
            "sortino_ratio": result.sortino_ratio,
            "expectancy": result.expectancy,
        }
        return table.get(self.objective_metric, table["sharpe_ratio"])

    @staticmethod
    def _grid_rows(param_grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
        """Expand a named grid into one dict per Cartesian combination."""
        names = list(param_grid)
        if not names:
            return [{}]
        return [
            dict(zip(names, row))
            for row in _cartesian_product(*(param_grid[n] for n in names))
        ]

    def _run_single_backtest(
        self, data: list[KlineEvent], params: dict[str, Any], symbol: str
    ) -> tuple[float, BacktestResult]:
        """One trial body: build, simulate, score."""
        outcome = self._create_strategy(params, symbol).run_backtest(data)
        return self._get_metric_value(outcome), outcome

    @staticmethod
    def _improves(candidate: float, incumbent: float, maximize: bool) -> bool:
        """Strict-better comparison shared by every search mode."""
        return candidate > incumbent if maximize else candidate < incumbent

    def grid_search(
        self,
        data: list[KlineEvent],
        param_grid: dict[str, list[Any]],
        symbol: str = "BTC/USDT",
        maximize: bool = True,
        verbose: bool = False,
    ) -> OptimizationResult:
        """Score every combination in ``param_grid`` and keep the best.

        Args:
            param_grid: Parameter name -> candidate values, e.g.
                ``{'rsi_period': [10, 14, 20]}``.
            maximize: Direction of the objective comparison.

        Returns:
            OptimizationResult whose ``n_trials`` equals the full combination
            count and whose ``trial_history`` records each evaluation.
        """
        started = time.time()
        combos = self._grid_rows(param_grid)

        # Champion starts at the worst possible score so any real trial wins.
        champion_value = float("-inf") if maximize else float("inf")
        champion_params: dict[str, Any] = {}
        champion_result: BacktestResult | None = None
        history: list[dict[str, Any]] = []

        for ordinal, params in enumerate(combos):
            metric_value, outcome = self._run_single_backtest(data, params, symbol)
            history.append(
                {"trial_id": ordinal, "params": params, "metric_value": metric_value}
            )
            if self._improves(metric_value, champion_value, maximize):
                champion_value = metric_value
                champion_params = params
                champion_result = outcome
            if verbose and ordinal % 10 == 0:
                print(
                    f"Trial {ordinal}/{len(combos)}: {self.objective_metric}={metric_value:.4f}"
                )

        return OptimizationResult(
            best_params=champion_params,
            best_value=champion_value,
            best_result=champion_result,
            n_trials=len(combos),
            optimization_time=time.time() - started,
            trial_history=history,
        )

    def _make_sampler(self, sampler: str, seed: int | None) -> Any:
        """Sampler factory honoring a reproducibility seed when provided."""
        factories = {
            "random": optuna.samplers.RandomSampler,
            "cmaes": optuna.samplers.CmaEsSampler,
        }
        factory = factories.get(sampler, TPESampler)
        return factory(seed=seed)

    def _suggest_parameters(
        self, trial: "optuna.Trial", param_space: dict[str, Any]
    ) -> dict[str, Any]:
        """Sample one point from ``param_space`` for this trial.

        Two-value tuples become int or float ranges depending on endpoint
        types; lists become categorical choices; anything else is a config
        error.
        """
        picked: dict[str, Any] = {}
        for name, spec in param_space.items():
            if isinstance(spec, tuple) and len(spec) == 2:
                low, high = spec
                if isinstance(low, int) and isinstance(high, int):
                    picked[name] = trial.suggest_int(name, low, high)
                else:
                    picked[name] = trial.suggest_float(name, float(low), float(high))
            elif isinstance(spec, list):
                picked[name] = trial.suggest_categorical(name, spec)
            else:
                raise ValueError(f"Invalid param_space for {name}: {spec}")
        return picked

    def _score_study_trials(
        self, study: "optuna.Study"
    ) -> list[tuple[float, dict[str, Any]]]:
        """Collect completed trials' (value, params); empty when all pruned.

        A study where every trial was pruned (e.g. the min_trades gate) has no
        usable best_trial, so it yields nothing rather than raising.
        """
        completed = [
            t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE
        ]
        ranked: list[tuple[float, dict[str, Any]]] = []
        for t in completed:
            if t.value is not None:
                ranked.append((float(t.value), t.params))
        return ranked

    @staticmethod
    def _pick_seed_champion(
        seed_params: list[tuple[float, dict[str, Any]]],
        maximize: bool,
    ) -> tuple[float, dict[str, Any]]:
        """Choose the winning (value, params) pair across seeds."""
        values = [v for v, _ in seed_params]
        winner_slot = int(np.argmax(values)) if maximize else int(np.argmin(values))
        return seed_params[winner_slot]

    def _resolve_report_periods(
        self, periods_per_year: int | None, data_interval: str
    ) -> int:
        """OQ-2 ruling (2026-09-28): DSR/PBO annualization must match the
        sharpe series of the same run. Explicit argument wins, then the
        domain calendar, then the constructor knob, then the CRYPTO table
        (365 daily fallback) — the legacy bare-call 252 is retired."""
        if periods_per_year is not None:
            return periods_per_year
        if self.market_domain is not None:
            return int(_domain_annualization(self.market_domain, data_interval))
        if self.periods_per_year is not None:
            return self.periods_per_year
        return int(_domain_annualization(MarketDomain.CRYPTO, data_interval))

    def _build_overfit_report(
        self,
        best_result: BacktestResult | None,
        total_trials: int,
        periods_per_year: int | None,
        data_interval: str = "",
        trials_matrix: np.ndarray | None = None,
    ) -> dict[str, Any] | None:
        """Optionally attach a DSR/PBO report; failures degrade to None.

        ``trials_matrix`` is the (T x N) per-period returns of surviving
        trials. Without it PBO is structurally uncomputable (CSCV needs at
        least two competing configurations), so the report carries
        ``pbo=None`` — PBO only becomes meaningful once the search has
        actually ranked alternatives against each other.
        """
        if not self.overfit_check or best_result is None:
            return None
        try:
            from selene_space_backtest.overfit import build_overfit_report

            equity = np.asarray(best_result.equity_curve, dtype=float)
            if len(equity) <= 2:
                return None
            returns = np.diff(equity) / equity[:-1]
            report = build_overfit_report(
                selected_returns=returns,
                n_trials=total_trials,
                trials_matrix=trials_matrix,
                periods_per_year=self._resolve_report_periods(
                    periods_per_year, data_interval
                ),
            )
            if not report.passed:
                import logging

                logging.getLogger(__name__).warning(
                    "Overfit detected: %s (DSR=%.3f, PBO=%s)",
                    report.verdict,
                    report.deflated_sharpe_ratio,
                    f"{report.pbo:.3f}" if report.pbo is not None else "N/A",
                )
            return dict(report.to_dict())
        except Exception:
            return None

    def bayesian_optimize(
        self,
        data: list[KlineEvent],
        param_space: dict[str, Any],
        symbol: str = "BTC/USDT",
        n_trials: int = 50,
        maximize: bool = True,
        timeout: int | None = None,
        n_jobs: int = 1,
        sampler: str = "tpe",
        verbose: bool = False,
        seed: int | None = None,
        n_seeds: int = 1,
        periods_per_year: int | None = None,
    ) -> OptimizationResult:
        """Optuna-powered search, optionally replicated across several seeds.

        Args:
            param_space: Name -> (low, high) range or explicit choice list.
            timeout: Wall-clock ceiling in seconds for the whole call.
            seed: Study i draws with seed + i; None keeps optuna's default
                nondeterminism.
            n_seeds: Independent replications. ``best_params`` belongs to the
                strongest surviving replication, ``best_value`` averages them,
                and ``seed_values``/``seed_std`` quantify spread. Replications
                whose trials were all pruned are skipped, not fatal.

        Returns:
            OptimizationResult aggregating all studies' trial history.
        """
        started = time.time()
        n_seeds = max(1, int(n_seeds))
        direction = "maximize" if maximize else "minimize"

        # Per-trial equity series for PBO's CSCV. Every trial replays the same
        # bar window, so all columns share one length and can be stacked as-is.
        # Bounded: a large sweep (hundreds of trials x thousands of bars) would
        # otherwise pin tens of MB for a statistic nobody reads mid-run.
        trial_returns: list[np.ndarray] = []
        pbo_cap = _PBO_MAX_TRIALS

        def objective(trial: "optuna.Trial") -> float:
            chosen = self._suggest_parameters(trial, param_space)
            outcome = self._create_strategy(chosen, symbol).run_backtest(data)

            # Sparse-fill trials carry no signal — prune instead of scoring.
            if self.min_trades > 0 and outcome.total_trades < self.min_trades:
                raise optuna.TrialPruned()

            metric_value = self._get_metric_value(outcome)
            if self.overfit_check and len(trial_returns) < pbo_cap:
                trial_returns.append(_period_returns(outcome.equity_curve))
            if verbose:
                print(
                    f"Trial {trial.number}: params={chosen}, "
                    f"{self.objective_metric}={metric_value:.4f}"
                )
            return metric_value

        surviving_values: list[float] = []
        surviving_pairs: list[tuple[float, dict[str, Any]]] = []
        # Per-study extraction: each study's trials are flattened to light
        # dicts as soon as that study finishes — holding every optuna.Trial
        # object (with its distributions/user-attrs) across all seeds keeps
        # memory growing linearly with trial count for zero benefit.
        history: list[dict[str, Any]] = []

        for replica in range(n_seeds):
            replica_seed = None if seed is None else seed + replica
            study = optuna.create_study(
                direction=direction,
                sampler=self._make_sampler(sampler, replica_seed),
            )
            study.optimize(
                objective,
                n_trials=n_trials,
                timeout=timeout,
                n_jobs=n_jobs,
                show_progress_bar=verbose,
            )

            pairs = self._score_study_trials(study)
            history.extend(
                {"trial_id": t.number, "params": t.params, "metric_value": t.value}
                for t in study.trials
            )
            del study

            if not pairs:
                continue
            pair_values = [v for v, _ in pairs]
            winner_slot = (
                int(np.argmax(pair_values)) if maximize else int(np.argmin(pair_values))
            )
            surviving_values.append(pair_values[winner_slot])
            champion = pairs[winner_slot]
            surviving_pairs.append((champion[0], champion[1]))

        total_trials = len(history)

        # Every replication failed the gate — report the emptiness honestly.
        if not surviving_values:
            return OptimizationResult(
                best_params={},
                best_value=float("-inf") if maximize else float("inf"),
                best_result=None,
                n_trials=total_trials,
                optimization_time=time.time() - started,
                trial_history=history,
                overfit_report=None,
                seed_values=[],
                seed_std=0.0,
                all_rejected=True,
            )

        mean_value = float(np.mean(surviving_values))
        spread = float(np.std(surviving_values))
        _, champion_params = self._pick_seed_champion(surviving_pairs, maximize)

        champion_result = self._create_strategy(
            dict(champion_params), symbol
        ).run_backtest(data)
        overfit_report = self._build_overfit_report(
            champion_result,
            total_trials,
            periods_per_year,
            data_interval=data[0].interval if data else "",
            trials_matrix=_stack_trial_returns(trial_returns),
        )

        return OptimizationResult(
            best_params=dict(champion_params),
            best_value=mean_value,
            best_result=champion_result,
            n_trials=total_trials,
            optimization_time=time.time() - started,
            trial_history=history,
            overfit_report=overfit_report,
            seed_values=surviving_values,
            seed_std=spread,
        )

    def walk_forward_validate(
        self,
        data: list[KlineEvent],
        param_space: dict[str, Any],
        symbol: str = "BTC/USDT",
        n_folds: int = 5,
        train_ratio: float = 0.7,
        n_trials_per_fold: int = 30,
        maximize: bool = True,
        verbose: bool = False,
    ) -> WalkForwardResult:
        """Tune inside each fold, then grade the winner on unseen tail bars.

        The series is cut into ``n_folds`` contiguous chunks; per chunk the
        leading ``train_ratio`` portion is tuned on (in-sample) and the rest
        graded out-of-sample. IS/OOS gaps summarize overfitting risk.

        Raises:
            ValueError: When fewer than ``n_folds * 10`` bars are supplied.
        """
        if len(data) < n_folds * 10:
            raise ValueError(
                f"Data too short for {n_folds} folds. Need at least {n_folds * 10} bars."
            )

        fold_size = len(data) // n_folds
        fold_reports: list[dict[str, Any]] = []

        # Parallel IS/OOS score accumulators, filled per fold.
        is_sharpes: list[float] = []
        oos_sharpes: list[float] = []
        is_returns: list[float] = []
        oos_returns: list[float] = []

        for fold_idx in range(n_folds):
            if verbose:
                print(f"\nFold {fold_idx + 1}/{n_folds}")

            lo = fold_idx * fold_size
            hi = min((fold_idx + 1) * fold_size, len(data))
            window = data[lo:hi]
            split_at = int(len(window) * train_ratio)
            train_slice, test_slice = window[:split_at], window[split_at:]

            if verbose:
                print(f"IS: {len(train_slice)} bars, OOS: {len(test_slice)} bars")

            # Tune strictly on the leading portion of the fold...
            tuned = self.bayesian_optimize(
                param_space=param_space,
                symbol=symbol,
                n_trials=n_trials_per_fold,
                maximize=maximize,
                verbose=False,
                data=train_slice,
            )
            # ...then grade the winner on both halves.
            in_sample_run = self._create_strategy(
                tuned.best_params, symbol
            ).run_backtest(train_slice)
            out_sample_run = self._create_strategy(
                tuned.best_params, symbol
            ).run_backtest(test_slice)

            is_sharpes.append(in_sample_run.sharpe_ratio)
            oos_sharpes.append(out_sample_run.sharpe_ratio)
            is_returns.append(in_sample_run.total_return)
            oos_returns.append(out_sample_run.total_return)

            fold_reports.append(
                {
                    "fold_idx": fold_idx,
                    "best_params": tuned.best_params,
                    "is_sharpe": in_sample_run.sharpe_ratio,
                    "oos_sharpe": out_sample_run.sharpe_ratio,
                    "is_return": in_sample_run.total_return,
                    "oos_return": out_sample_run.total_return,
                    "is_trades": in_sample_run.total_trades,
                    "oos_trades": out_sample_run.total_trades,
                }
            )

            if verbose:
                print(f"Best params: {tuned.best_params}")
                print(
                    f"IS Sharpe: {in_sample_run.sharpe_ratio:.4f}, OOS Sharpe: {out_sample_run.sharpe_ratio:.4f}"
                )
                print(
                    f"IS Return: {in_sample_run.total_return:.2%}, OOS Return: {out_sample_run.total_return:.2%}"
                )

        stability = (
            float(np.corrcoef(is_sharpes, oos_sharpes)[0, 1])
            if len(is_sharpes) > 1 and len(oos_sharpes) > 1
            else 0.0
        )

        return WalkForwardResult(
            folds=fold_reports,
            avg_is_sharpe=float(np.mean(is_sharpes)) if is_sharpes else 0.0,
            avg_oos_sharpe=float(np.mean(oos_sharpes)) if oos_sharpes else 0.0,
            avg_is_return=float(np.mean(is_returns)) if is_returns else 0.0,
            avg_oos_return=float(np.mean(oos_returns)) if oos_returns else 0.0,
            stability_score=stability,
        )


def optimize_strategy(
    strategy_class: type[BacktestStrategyTemplate],
    data: list[KlineEvent],
    param_space: dict[str, Any],
    method: str = "bayesian",
    n_trials: int = 50,
    **kwargs: Any,
) -> OptimizationResult:
    """One-call entry point: pick a method and hand the rest to the optimizer.

    Raises:
        ValueError: On a ``method`` other than 'bayesian' or 'grid'.
    """
    engine = StrategyOptimizer(strategy_class, **kwargs)

    if method == "bayesian":
        return engine.bayesian_optimize(data, param_space, n_trials=n_trials)
    if method == "grid":
        lattice: dict[str, list[Any]] = {}
        for name, spec in param_space.items():
            if isinstance(spec, tuple) and len(spec) == 2:
                low, high = spec
                lattice[name] = (
                    list(range(low, high + 1))
                    if isinstance(low, int) and isinstance(high, int)
                    else list(np.linspace(float(low), float(high), 10))
                )
            elif isinstance(spec, list):
                lattice[name] = spec
        return engine.grid_search(data, lattice)
    raise ValueError(f"Unknown method: {method}")

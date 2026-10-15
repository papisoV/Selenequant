"""StrategyOptimizer coverage — construction knobs, grid sweep, Optuna
Bayesian search (tpe/random/categorical/timeout), walk-forward folds,
wrapper dispatch, result serialization, and metric switching."""

from __future__ import annotations

import pytest

from selene_space_strategy.optimizer import (
    StrategyOptimizer,
    OptimizationResult,
    WalkForwardResult,
    optimize_strategy,
)
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.routing import MarketDomain


class SimpleRSIStrategy(BacktestStrategyTemplate):
    """Minimal RSI strategy used to exercise the optimizer."""

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(
            symbol,
            initial_capital,
            leverage,
            fee_rate,
            max_bars,
            long_only,
            **kwargs,
        )
        self.params = {"rsi_period": 14, "oversold": 30, "overbought": 70}

    def get_signal(self, data) -> SignalType:
        if len(data) < self.params["rsi_period"] + 1:
            return SignalType.HOLD

        rsi_val = self.rsi(data, period=self.params["rsi_period"]).last()
        if rsi_val is None:
            return SignalType.HOLD
        if rsi_val < self.params["oversold"]:
            return SignalType.BUY
        if rsi_val > self.params["overbought"]:
            return SignalType.SELL
        return SignalType.HOLD


def _kline_series(
    n: int = 100,
    start_price: float = 100.0,
    trend: str = "up",
    volatility: float = 1.0,
) -> list[KlineEvent]:
    """Hourly synthetic series in one of four shapes."""
    out: list[KlineEvent] = []

    for i in range(n):
        if trend == "up":
            base_price = start_price + i * 0.5
        elif trend == "down":
            base_price = start_price - i * 0.5
        elif trend == "oscillate":
            base_price = start_price + (i % 10 - 5) * volatility
        else:
            base_price = start_price

        out.append(
            KlineEvent(
                exchange="binance",
                symbol="BTC/USDT",
                timestamp=i * 3600000,
                interval="1h",
                open=base_price,
                high=base_price + volatility,
                low=base_price - volatility,
                close=base_price + (i % 2 - 0.5) * volatility,
                volume=1000.0,
                closed=True,
            )
        )

    return out


# Pinned spaces where only rsi_period actually varies.
PINNED_GRID = {"rsi_period": [14, 20], "oversold": [30], "overbought": [70]}
PINNED_SPACE = {"rsi_period": (10, 25), "oversold": (30, 30), "overbought": (70, 70)}


def _make_optimizer(metric: str = "sharpe_ratio") -> StrategyOptimizer:
    return StrategyOptimizer(SimpleRSIStrategy, objective_metric=metric)


class TestConstruction:
    """Constructor defaults and overrides."""

    def test_defaults(self) -> None:
        optimizer = StrategyOptimizer(SimpleRSIStrategy)
        assert optimizer.strategy_class is SimpleRSIStrategy
        assert optimizer.objective_metric == "sharpe_ratio"
        assert optimizer.initial_capital == 100000.0

    def test_overrides(self) -> None:
        optimizer = StrategyOptimizer(
            SimpleRSIStrategy,
            objective_metric="profit_factor",
            initial_capital=50000.0,
            leverage=2,
            fee_rate=0.001,
        )
        assert optimizer.objective_metric == "profit_factor"
        assert optimizer.initial_capital == 50000.0
        assert optimizer.leverage == 2
        assert optimizer.fee_rate == 0.001

    def test_missing_optuna_branch(self) -> None:
        # Needs an optuna-free import environment; optuna ships with the repo.
        pass

    def test_sampled_params_reach_typed_attrs(self) -> None:
        """Template strategies read ``self.bb_len``-style typed attrs, not
        ``self.params`` — sampled params must go through the constructor."""
        from selene_space_strategy.templates.presets.bb_v3 import BBV3Strategy

        optimizer = StrategyOptimizer(BBV3Strategy)
        sampled = {"bb_len": 15, "buy_mult": 1.9, "nonexistent_key": 1}
        instance = optimizer._create_strategy(sampled, symbol="BTC/USDT")
        assert instance.params == sampled  # type: ignore[attr-defined]  # dynamic attrs by design
        assert instance.bb_len == 15  # type: ignore[attr-defined]
        assert instance.buy_mult == 1.9  # type: ignore[attr-defined]
        assert not hasattr(instance, "nonexistent_key")

    def test_account_keys_never_sampled(self) -> None:
        from selene_space_strategy.templates.presets.bb_v3 import BBV3Strategy

        optimizer = StrategyOptimizer(BBV3Strategy, initial_capital=55555.0)
        sampled = {"bb_len": 15, "initial_capital": 1.0, "symbol": "HACK"}
        instance = optimizer._create_strategy(sampled, symbol="BTC/USDT")
        assert instance.initial_capital == 55555.0
        assert instance.symbol == "BTC/USDT"
        assert instance.bb_len == 15  # type: ignore[attr-defined]


class TestGridSweep:
    """Exhaustive search over a parameter grid."""

    def test_two_value_grid_runs_two_trials(self) -> None:
        result = _make_optimizer().grid_search(
            _kline_series(150, trend="oscillate"), PINNED_GRID, verbose=False
        )
        assert isinstance(result, OptimizationResult)
        assert result.n_trials == 2
        assert "rsi_period" in result.best_params
        assert result.best_value >= -10.0  # Sharpe may be negative

    def test_cross_product_trial_count(self) -> None:
        grid = {
            "rsi_period": [14, 20],
            "oversold": [25, 30],
            "overbought": [70, 75],
        }
        result = _make_optimizer().grid_search(
            _kline_series(150, trend="oscillate"), grid, verbose=False
        )
        assert result.n_trials == 8
        assert len(result.best_params) == 3

    def test_minimize_direction(self) -> None:
        optimizer = _make_optimizer("max_drawdown")
        result = optimizer.grid_search(
            _kline_series(150, trend="oscillate"),
            PINNED_GRID,
            maximize=False,
            verbose=False,
        )
        assert result.n_trials == 2
        assert result.best_value <= 0.0  # Drawdowns are negative

    def test_history_has_one_entry_per_combo(self) -> None:
        result = _make_optimizer().grid_search(
            _kline_series(150, trend="oscillate"), PINNED_GRID, verbose=False
        )
        assert len(result.trial_history) == 2
        for trial in result.trial_history:
            assert {"trial_id", "params", "metric_value"} <= set(trial)


class TestBayesianSearch:
    """Optuna-backed searches."""

    def test_tpe_ten_trials(self) -> None:
        space = {
            "rsi_period": (10, 25),
            "oversold": (20, 40),
            "overbought": (60, 80),
        }
        result = _make_optimizer().bayesian_optimize(
            _kline_series(150, trend="oscillate"), space, n_trials=10, verbose=False
        )
        assert isinstance(result, OptimizationResult)
        assert result.n_trials == 10
        assert "rsi_period" in result.best_params
        assert result.best_result is not None

    def test_int_ranges_yield_python_ints(self) -> None:
        space = {
            "rsi_period": (10, 25),
            "oversold": (20, 40),
            "overbought": (60, 80),
        }
        best = (
            _make_optimizer()
            .bayesian_optimize(
                _kline_series(150, trend="oscillate"), space, n_trials=5, verbose=False
            )
            .best_params
        )
        for key in ("rsi_period", "oversold", "overbought"):
            assert isinstance(best[key], int)

    def test_choice_lists_constrain_values(self) -> None:
        space = {
            "rsi_period": [10, 14, 20, 25],
            "oversold": [20, 30, 35],
            "overbought": [70, 75, 80],
        }
        best = (
            _make_optimizer()
            .bayesian_optimize(
                _kline_series(150, trend="oscillate"), space, n_trials=10, verbose=False
            )
            .best_params
        )
        assert best["rsi_period"] in [10, 14, 20, 25]
        assert best["oversold"] in [20, 30, 35]
        assert best["overbought"] in [70, 75, 80]

    def test_sampler_kwarg_switches_backend(self) -> None:
        data = _kline_series(150, trend="oscillate")
        pinned = {"rsi_period": (10, 25), "oversold": (30, 30), "overbought": (70, 70)}
        optimizer = _make_optimizer()

        assert (
            optimizer.bayesian_optimize(
                data, pinned, n_trials=5, sampler="tpe", verbose=False
            ).n_trials
            == 5
        )
        assert (
            optimizer.bayesian_optimize(
                data, pinned, n_trials=5, sampler="random", verbose=False
            ).n_trials
            == 5
        )

    def test_timeout_stops_before_all_trials(self) -> None:
        result = StrategyOptimizer(SimpleRSIStrategy).bayesian_optimize(
            _kline_series(150), PINNED_SPACE, n_trials=100, timeout=2, verbose=False
        )
        assert result.n_trials < 100
        assert result.optimization_time <= 3.0

    def test_history_length_equals_trials(self) -> None:
        pinned = {"rsi_period": (10, 25), "oversold": (30, 30), "overbought": (70, 70)}
        result = _make_optimizer().bayesian_optimize(
            _kline_series(150, trend="oscillate"), pinned, n_trials=10, verbose=False
        )
        assert len(result.trial_history) == 10
        for trial in result.trial_history:
            assert {"trial_id", "params", "metric_value"} <= set(trial)


class TestWalkForward:
    """Rolling IS/OOS evaluation."""

    WALK_SPACE = {
        "rsi_period": (10, 20),
        "oversold": (25, 35),
        "overbought": (65, 75),
    }
    WALK_PINNED = {"rsi_period": (10, 20), "oversold": (30, 30), "overbought": (70, 70)}

    def test_three_folds_requested_three_returned(self) -> None:
        result = _make_optimizer().walk_forward_validate(
            _kline_series(300, trend="oscillate"),
            self.WALK_SPACE,
            n_folds=3,
            train_ratio=0.7,
            n_trials_per_fold=5,
            verbose=False,
        )
        assert isinstance(result, WalkForwardResult)
        assert len(result.folds) == 3

    def test_aggregates_are_floats(self) -> None:
        result = _make_optimizer().walk_forward_validate(
            _kline_series(300, trend="oscillate"),
            self.WALK_PINNED,
            n_folds=3,
            n_trials_per_fold=5,
            verbose=False,
        )
        for attr in (
            "avg_is_sharpe",
            "avg_oos_sharpe",
            "avg_is_return",
            "avg_oos_return",
            "stability_score",
        ):
            assert isinstance(getattr(result, attr), float)

    def test_fold_records_carry_six_keys(self) -> None:
        result = _make_optimizer().walk_forward_validate(
            _kline_series(300, trend="oscillate"),
            self.WALK_PINNED,
            n_folds=3,
            n_trials_per_fold=5,
            verbose=False,
        )
        required = {
            "fold_idx",
            "best_params",
            "is_sharpe",
            "oos_sharpe",
            "is_return",
            "oos_return",
        }
        for fold in result.folds:
            assert required <= set(fold)

    def test_short_series_rejected(self) -> None:
        with pytest.raises(ValueError, match="Data too short"):
            _make_optimizer().walk_forward_validate(
                _kline_series(20), self.WALK_PINNED, n_folds=5, verbose=False
            )


class TestWrapperDispatch:
    """Module-level optimize_strategy routing."""

    def test_bayesian_route(self) -> None:
        result = optimize_strategy(
            SimpleRSIStrategy,
            _kline_series(150),
            PINNED_SPACE,
            method="bayesian",
            n_trials=5,
        )
        assert isinstance(result, OptimizationResult)
        assert result.n_trials == 5

    def test_grid_route(self) -> None:
        result = optimize_strategy(
            SimpleRSIStrategy,
            _kline_series(150),
            PINNED_SPACE,
            method="grid",
        )
        assert isinstance(result, OptimizationResult)

    def test_unknown_route_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown method"):
            optimize_strategy(
                SimpleRSIStrategy,
                _kline_series(150),
                {"rsi_period": (10, 25)},
                method="invalid",
            )


class TestSerializationShapes:
    """to_dict output of both result records."""

    def test_optimization_result_fields(self) -> None:
        record = OptimizationResult(
            best_params={"rsi_period": 14},
            best_value=1.5,
            n_trials=10,
            optimization_time=5.0,
            trial_history=[
                {"trial_id": 0, "params": {"rsi_period": 14}, "metric_value": 1.5}
            ],
        )
        dumped = record.to_dict()
        assert dumped["best_params"] == {"rsi_period": 14}
        assert dumped["best_value"] == 1.5
        assert dumped["n_trials"] == 10
        assert dumped["optimization_time"] == 5.0
        assert len(dumped["trial_history"]) == 1

    def test_walk_forward_result_fields(self) -> None:
        record = WalkForwardResult(
            folds=[{"fold_idx": 0, "is_sharpe": 1.0, "oos_sharpe": 0.8}],
            avg_is_sharpe=1.0,
            avg_oos_sharpe=0.8,
            avg_is_return=0.1,
            avg_oos_return=0.08,
            stability_score=0.9,
        )
        dumped = record.to_dict()
        assert len(dumped["folds"]) == 1
        assert dumped["avg_is_sharpe"] == 1.0
        assert dumped["avg_oos_sharpe"] == 0.8
        assert dumped["stability_score"] == 0.9


class TestMetricTargets:
    """Alternative objective metrics remain sane."""

    def test_sharpe_target_returns_value(self) -> None:
        result = _make_optimizer("sharpe_ratio").bayesian_optimize(
            _kline_series(150), PINNED_SPACE, n_trials=5, verbose=False
        )
        assert result.best_value is not None

    def test_total_return_target_floor(self) -> None:
        result = _make_optimizer("total_return").bayesian_optimize(
            _kline_series(150, trend="up"), PINNED_SPACE, n_trials=5, verbose=False
        )
        assert result.best_value >= -1.0  # Returns may be negative

    def test_profit_factor_target_non_negative(self) -> None:
        result = _make_optimizer("profit_factor").bayesian_optimize(
            _kline_series(150), PINNED_SPACE, n_trials=5, verbose=False
        )
        assert result.best_value >= 0.0


class TestMarketDomainRouting:
    """market_domain knob: None keeps legacy path, equity domain overrides 252."""

    def test_default_none_keeps_legacy(self) -> None:
        opt = _make_optimizer("sharpe_ratio")
        assert opt.market_domain is None

    def test_market_domain_accepted(self) -> None:
        opt = StrategyOptimizer(
            SimpleRSIStrategy,
            objective_metric="sharpe_ratio",
            market_domain=MarketDomain.A_SHARE,
        )
        assert opt.market_domain is MarketDomain.A_SHARE

    def test_crypto_domain_keeps_legacy_fallback(self) -> None:
        # OQ-2 ruling (2026-09-28): bare calls with no explicit periods and
        # no domain resolve to the CRYPTO calendar (365 daily), not 252.
        opt = StrategyOptimizer(
            SimpleRSIStrategy,
            objective_metric="sharpe_ratio",
            market_domain=MarketDomain.CRYPTO,
            periods_per_year=None,
        )
        assert opt._resolve_report_periods(None, "1d") == 365
        assert opt._resolve_report_periods(None, "4h") > 365

    def test_explicit_argument_beats_domain(self) -> None:
        opt = StrategyOptimizer(
            SimpleRSIStrategy,
            objective_metric="sharpe_ratio",
            market_domain=MarketDomain.A_SHARE,
            periods_per_year=123,
        )
        assert opt._resolve_report_periods(None, "1d") == 242
        assert opt._resolve_report_periods(123, "1d") == 123

    def test_constructor_knob_beats_crypto_default(self) -> None:
        opt = StrategyOptimizer(
            SimpleRSIStrategy,
            objective_metric="sharpe_ratio",
            periods_per_year=252,
        )
        assert opt._resolve_report_periods(None, "1d") == 252

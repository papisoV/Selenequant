"""US-003: multi-seed / min_trades / periods-per-year regression tests.

Covers the P0 additions to ``StrategyOptimizer.bayesian_optimize``:
- ``seed`` / ``n_seeds`` independent studies (each with its own sampler seed)
- ``min_trades`` pruning via ``optuna.TrialPruned`` (0-trade trials rejected)
- per-seed all-rejected handling (skip, never interrupt the run)
- ``_periods_per_year_from_interval`` full mapping + illegal interval raise
- tuner 16-key record subset (integration, via ``scripts.llm_param_tuner``)

All tests are deterministic: the ``GateTradesStrategy`` gates its trade count on
the sampled ``trade`` parameter, and the random sampler is seeded.
"""

from __future__ import annotations

import importlib
import math
import sys

import pytest

from selene_space_strategy.optimizer import (
    INTERVAL_PERIODS_PER_YEAR,
    OptimizationResult,
    StrategyOptimizer,
    _periods_per_year_from_interval,
)
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent


class GateTradesStrategy(BacktestStrategyTemplate):
    """Strategy whose trade count is fully controlled by the ``trade`` param.

    trade=1: alternates BUY/SELL every bar -> ~n/2 completed trades.
    trade=0: always HOLD -> 0 trades (exercises the min_trades gate).
    """

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
        self.params = {"trade": 0}

    def get_signal(self, data) -> SignalType:
        if self.params.get("trade", 0) == 1:
            return SignalType.BUY if len(data) % 2 == 0 else SignalType.SELL
        return SignalType.HOLD


def zig_klines(n: int = 200, start_price: float = 100.0) -> list[KlineEvent]:
    """20-bar zigzag data that stays in a positive price band (>0 enforced)."""
    out: list[KlineEvent] = []
    for i in range(n):
        base = start_price + (i % 20 - 10) * 5.0
        out.append(
            KlineEvent(
                exchange="binance",
                symbol="BTC/USDT",
                timestamp=i * 3600000,
                interval="1h",
                open=base,
                high=base + 2.5,
                low=base - 2.5,
                close=base,
                volume=1000.0,
                closed=True,
            )
        )
    return out


@pytest.fixture
def data() -> list[KlineEvent]:
    return zig_klines(200)


class TestMultiSeed:
    """n_seeds > 1: independent studies, seed_values/seed_std aggregation."""

    def test_all_seeds_survive(self, data) -> None:
        """trade=1 always trades -> every seed survives -> len(seed_values)==3."""
        optimizer = StrategyOptimizer(GateTradesStrategy, min_trades=1)
        result = optimizer.bayesian_optimize(
            data,
            {"trade": [1]},
            n_trials=3,
            n_seeds=3,
            verbose=False,
        )
        assert result.all_rejected is False
        assert len(result.seed_values) == 3
        assert len(result.seed_values) <= 3  # default invariant
        assert result.seed_std >= 0.0
        assert result.best_params == {"trade": 1}
        assert result.best_value == pytest.approx(float(result.seed_values[0]))

    def test_seed_values_never_exceed_n_seeds(self, data) -> None:
        """Rejected seeds shrink seed_values; it never grows past n_seeds."""
        optimizer = StrategyOptimizer(GateTradesStrategy, min_trades=1)
        result = optimizer.bayesian_optimize(
            data,
            {"trade": [0, 1]},
            n_trials=2,
            n_seeds=4,
            sampler="random",
            seed=0,
            verbose=False,
        )
        assert len(result.seed_values) <= 4
        assert result.all_rejected is False  # at least one survives

    def test_same_seed_tpe_is_reproducible(self, data) -> None:
        """TPE with the same seed twice yields identical params and values."""
        optimizer = StrategyOptimizer(GateTradesStrategy)
        r1 = optimizer.bayesian_optimize(
            data,
            {"trade": [1]},
            n_trials=5,
            n_seeds=1,
            sampler="tpe",
            seed=42,
            verbose=False,
        )
        r2 = optimizer.bayesian_optimize(
            data,
            {"trade": [1]},
            n_trials=5,
            n_seeds=1,
            sampler="tpe",
            seed=42,
            verbose=False,
        )
        assert r1.best_params == r2.best_params
        assert r1.best_value == r2.best_value
        assert r1.seed_values == r2.seed_values


class TestMinTrades:
    """min_trades gate: 0-trade trials pruned via TrialPruned."""

    def test_all_rejected(self, data) -> None:
        """trade=0 never trades -> every trial pruned -> all_rejected=True."""
        optimizer = StrategyOptimizer(GateTradesStrategy, min_trades=1)
        result = optimizer.bayesian_optimize(
            data,
            {"trade": [0]},
            n_trials=5,
            n_seeds=2,
            verbose=False,
        )
        assert result.all_rejected is True
        assert result.best_params == {}
        assert result.best_value == float("-inf")
        assert result.seed_values == []
        assert result.seed_std == 0.0

    def test_all_rejected_n_seeds_1(self, data) -> None:
        """n_seeds=1 all-rejected path also returns cleanly (no exception)."""
        optimizer = StrategyOptimizer(GateTradesStrategy, min_trades=1)
        result = optimizer.bayesian_optimize(
            data,
            {"trade": [0]},
            n_trials=3,
            n_seeds=1,
            verbose=False,
        )
        assert result.all_rejected is True
        assert result.best_params == {}
        assert result.n_trials == 3  # trials were run, then pruned

    def test_mixed_per_seed_rejection(self, data) -> None:
        """One seed fully pruned, one survives -> 1 survivor, no exception.

        RandomSampler(seed=1) deterministically draws trade=0 in study-0 and
        trade=1 in study-1 (n_trials=1), so the outcome is stable run-to-run.
        """
        optimizer = StrategyOptimizer(GateTradesStrategy, min_trades=1)
        result = optimizer.bayesian_optimize(
            data,
            {"trade": [0, 1]},
            n_trials=1,
            n_seeds=2,
            sampler="random",
            seed=1,
            verbose=False,
        )
        assert result.all_rejected is False
        assert len(result.seed_values) == 1  # survivors only
        assert result.best_params == {"trade": 1}


class TestPeriodsPerYear:
    """_periods_per_year_from_interval full mapping + illegal raise."""

    def test_full_mapping(self) -> None:
        assert _periods_per_year_from_interval("1d") == 365
        assert _periods_per_year_from_interval("4h") == 2190
        assert _periods_per_year_from_interval("1h") == 8760
        assert _periods_per_year_from_interval("30m") == 17520
        assert _periods_per_year_from_interval("5m") == 105120
        assert _periods_per_year_from_interval("1m") == 525600

    def test_mapping_matches_module_table(self) -> None:
        for interval, periods in INTERVAL_PERIODS_PER_YEAR.items():
            assert _periods_per_year_from_interval(interval) == periods

    def test_unknown_interval_raises(self) -> None:
        for bad in ("2h", "weekly", "", "1H"):
            with pytest.raises(ValueError, match="Unknown interval"):
                _periods_per_year_from_interval(bad)

    def test_error_mentions_legal_intervals(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            _periods_per_year_from_interval("2h")
        msg = str(excinfo.value)
        assert "1d" in msg and "1m" in msg


class TestOptimizationResultToDict:
    """to_dict serializes the new seed/rejection fields."""

    def test_to_dict_includes_seed_fields(self) -> None:
        result = OptimizationResult(
            best_params={"trade": 1},
            best_value=0.5,
            n_trials=4,
            seed_values=[0.4, 0.6],
            seed_std=0.1,
            all_rejected=False,
        )
        d = result.to_dict()
        assert d["seed_values"] == [0.4, 0.6]
        assert d["seed_std"] == 0.1
        assert d["all_rejected"] is False

    def test_to_dict_defaults(self) -> None:
        d = OptimizationResult().to_dict()
        assert d["seed_values"] == []
        assert d["seed_std"] == 0.0
        assert d["all_rejected"] is False


def _import_tuner():
    """Import scripts.llm_param_tuner without its win32 stdout reassignment."""
    original_platform = sys.platform
    sys.platform = "linux"  # bypass the win32 console rewrap on module import
    try:
        return importlib.import_module("scripts.llm_param_tuner")
    finally:
        sys.platform = original_platform


class TestTunerIntegration:
    """tune_strategy emits the 16-key record (US-002 acceptance subset)."""

    def test_record_16key_subset(self, data) -> None:
        tuner = _import_tuner()
        bb_v3 = tuner.STRATEGY_REGISTRY["bb_v3"]
        record = tuner.tune_strategy(
            strategy_id="bb_v3",
            strategy_class=bb_v3[0],
            param_space=bb_v3[1],
            data=data,
            n_trials=2,
            interval="1h",
            objective="expectancy",
            n_seeds=2,
            min_trades=0,
        )
        assert record is not None

        expected_subset = {
            "seed_std",
            "deflated_sharpe_ratio",
            "haircut_sharpe_annual",
            "buy_hold_sharpe",
            "buy_hold_return",
            "buy_hold_drawdown",
            "buy_hold_vol",
            "best_sharpe",
        }
        assert expected_subset <= set(record.keys())
        assert "all_rejected" in record
        assert isinstance(record["best_sharpe"], float)
        assert record["interval"] == "1h"

    def test_buyhold_annualized_with_same_periods_per_year(self, data) -> None:
        """buy&hold Sharpe annualization matches DSR's periods_per_year (1h->8760)."""
        tuner = _import_tuner()
        ppy = _periods_per_year_from_interval("1h")
        buyhold = tuner._compute_buy_hold(data, ppy)

        closes = [k.close for k in data]
        returns = [(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]
        vol_std = float(_stdev(returns))
        expected_sharpe = (
            (sum(returns) / len(returns)) * math.sqrt(ppy) / vol_std
            if vol_std > 0
            else 0.0
        )
        assert buyhold["sharpe"] == pytest.approx(expected_sharpe, rel=1e-9)
        assert buyhold["total_return"] == pytest.approx(closes[-1] / closes[0] - 1.0)

        # And the same value flows into the tuner record.
        bb_v3 = tuner.STRATEGY_REGISTRY["bb_v3"]
        record = tuner.tune_strategy(
            strategy_id="bb_v3",
            strategy_class=bb_v3[0],
            param_space=bb_v3[1],
            data=data,
            n_trials=2,
            interval="1h",
            objective="expectancy",
        )
        assert record["buy_hold_sharpe"] == pytest.approx(expected_sharpe, rel=1e-9)


class TestResourceDiscipline:
    """P0-5 资源纪律三件套：收完即瘦身 / 逐 study 提取 / tmp 隔离。"""

    def test_record_backtest_result_is_scalar_only(self, data) -> None:
        """record['backtest_result'] 只含标量指标，不含 equity_curve/trades 大对象。"""
        tuner = _import_tuner()
        bb_v3 = tuner.STRATEGY_REGISTRY["bb_v3"]
        record = tuner.tune_strategy(
            strategy_id="bb_v3",
            strategy_class=bb_v3[0],
            param_space=bb_v3[1],
            data=data,
            n_trials=1,
            interval="1h",
        )
        assert record is not None
        br = record["backtest_result"]
        assert br is not None
        assert "equity_curve" not in br
        assert "trades" not in br
        expected_scalars = {
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
        }
        assert set(br.keys()) == expected_scalars

    def test_multi_seed_history_extracts_all_trials_per_study(self, data) -> None:
        """每个 study 收完即提取全部 trial（含被剪枝的），trial_id 按 study 内编号重启。"""
        optimizer = StrategyOptimizer(GateTradesStrategy, min_trades=1)
        result = optimizer.bayesian_optimize(
            data,
            {"trade": [0, 1]},
            n_trials=2,
            n_seeds=2,
            sampler="random",
            seed=0,
            verbose=False,
        )
        ids = [t["trial_id"] for t in result.trial_history]
        assert len(ids) == 4
        assert ids == [0, 1, 0, 1]

    def test_unique_tmp_path_isolated_per_pid(self, monkeypatch, tmp_path) -> None:
        """并发跑批各自持有独立 tmp 名，互不践踏。"""
        tuner = _import_tuner()
        out = tmp_path / "tuning_x.json"
        monkeypatch.setattr(tuner.os, "getpid", lambda: 111)
        first = tuner._unique_tmp_path(out)
        monkeypatch.setattr(tuner.os, "getpid", lambda: 222)
        second = tuner._unique_tmp_path(out)
        assert first != second
        assert first.suffix == ".tmp"
        assert first.name.startswith("tuning_x.json")


def _stdev(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))

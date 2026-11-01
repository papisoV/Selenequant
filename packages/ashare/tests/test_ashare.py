"""Tests for selene_space_ashare package — factor calculation, evaluation, regime, composite, strategy, backtest."""

import logging
import sys

import numpy as np
import pandas as pd
import pytest

from selene_space_ashare.factor_calc import (
    calculate_technical_factors,
    momentum,
    rsi,
    golden_cross,
    volume_climax,
    TECHNICAL_CALCULATORS,
)
from selene_space_ashare.factor_eval import (
    calc_forward_returns,
    calc_ic,
    factor_evaluation_report,
    factor_correlation_matrix,
    quantile_returns,
)
from selene_space_ashare.factors import (
    ALL_FACTORS,
    get_factors_by_subcategory,
    get_technical_factors,
    get_fundamental_factors,
    get_sentiment_factors,
    get_event_factors,
)
from selene_space_ashare.regime import (
    calc_index_regime,
    get_regime_on_date,
    should_trade,
)
from selene_space_ashare.composite import (
    compute_ic_weights,
    build_composite_panel,
    momentum_quality_composite,
    mean_reversion_composite,
    MOMENTUM_QUALITY_FACTORS,
    MEAN_REVERSION_FACTORS,
)
from selene_space_ashare.strategy import (
    calc_position_sizes,
    run_strategy,
    strategy_performance_metrics,
    make_trend_follow_strategy,
    make_mean_reversion_strategy,
)
from selene_space_ashare.backtest import (
    construct_portfolio,
    calc_performance_metrics,
    backtest_factor,
    backtest_factors_batch,
    backtest_composite,
    backtest_with_regime,
    backtest_full_strategy,
)
from selene_space_ashare.sentiment import (
    calc_limit_up_breadth,
    calc_seal_rate,
    calc_limit_up_ratio,
    calc_max_consecutive_boards,
    calc_reason_cluster,
    calc_stock_limit_up_factor,
)
from selene_space_types.stock import (
    FactorCategory,
    TrendRegime,
    VolRegime,
    RegimeState,
    CompositeConfig,
    StrategyConfigAshare,
)
from selene_space_ashare.fundamentals_pit import attach_fundamentals
from selene_space_ashare.data_fetcher import (
    filter_universe_by_history,
    get_csi500_constituents,
    get_hs300_history,
)


# ─── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def klines() -> pd.DataFrame:
    """Synthetic daily klines for testing."""
    np.random.seed(42)
    n = 300
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 10 + np.cumsum(np.random.randn(n) * 0.1)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close - np.random.rand(n) * 0.2,
            "high": close + np.random.rand(n) * 0.3,
            "low": close - np.random.rand(n) * 0.3,
            "close": close,
            "volume": np.random.randint(100_000, 1_000_000, n).astype(float),
        }
    )


@pytest.fixture
def index_daily() -> pd.DataFrame:
    """Synthetic index daily data."""
    np.random.seed(99)
    n = 500
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    close = 3000 + np.cumsum(np.random.randn(n) * 10)
    return pd.DataFrame({"date": dates, "close": close})


@pytest.fixture
def cross_section() -> pd.DataFrame:
    """Synthetic cross-sectional data for backtesting."""
    np.random.seed(7)
    dates = pd.date_range("2024-06-01", periods=60, freq="B")
    symbols = [f"S{i:06d}" for i in range(30)]
    rows = []
    for d in dates:
        for s in symbols:
            rows.append(
                {
                    "date": d,
                    "symbol": s,
                    "MOMENTUM_1M": np.random.randn(),
                    "VOLATILITY_20D": abs(np.random.randn()) * 0.3,
                    "MA_DEVIATION": np.random.randn() * 0.05,
                    "HIGH_52W_PROXIMITY": np.random.rand(),
                    "RSI_14D": np.random.rand() * 100,
                    "BB_WIDTH": abs(np.random.randn()) * 0.1,
                    "VOLUME_CLIMAX": np.random.randint(0, 2),
                    "fwd_5d": np.random.randn() * 0.03,
                    "fwd_1d": np.random.randn() * 0.01,
                }
            )
    return pd.DataFrame(rows)


# ─── Factor Definitions ──────────────────────────────────────────────


class TestFactorDefinitions:
    def test_all_factors_count(self):
        assert len(ALL_FACTORS) == 42  # batch4: +GP_MARGIN/LEVERAGE/CURRENT_RATIO/SIZE

    def test_factor_categories(self):
        tech = get_technical_factors()
        fund = get_fundamental_factors()
        sent = get_sentiment_factors()
        event = get_event_factors()
        assert len(tech) == 18
        assert len(fund) == 18  # batch4: +4
        assert len(sent) == 3
        assert len(event) == 3
        # No overlap
        all_names = set(tech) | set(fund) | set(sent) | set(event)
        assert all_names == set(ALL_FACTORS.keys())

    def test_factor_has_required_fields(self):
        for name, f in ALL_FACTORS.items():
            assert f.name == name
            assert isinstance(f.category, FactorCategory)
            assert f.direction in (1, -1)
            assert f.description

    def test_get_by_subcategory(self):
        momentum = get_factors_by_subcategory("momentum")
        assert "MOMENTUM_1M" in momentum
        assert "RSI_14D" not in momentum


# ─── Factor Calculation ──────────────────────────────────────────────


class TestFactorCalc:
    def test_calculate_technical_factors(self, klines):
        result = calculate_technical_factors(klines)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(klines)
        # Should have all TECHNICAL_CALCULATORS columns + date
        expected_cols = set(TECHNICAL_CALCULATORS.keys()) | {"date"}
        assert expected_cols.issubset(set(result.columns))

    def test_momentum(self, klines):
        m = momentum(klines, 21)
        assert isinstance(m, pd.Series)
        assert len(m) == len(klines)

    def test_rsi_range(self, klines):
        r = rsi(klines, 14)
        valid = r.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_golden_cross_binary(self, klines):
        gc = golden_cross(klines)
        valid = gc.dropna()
        assert set(valid.unique()).issubset({0, 1})

    def test_volume_climax_binary(self, klines):
        vc = volume_climax(klines)
        valid = vc.dropna()
        assert set(valid.unique()).issubset({0, 1})

    def test_empty_klines(self):
        empty = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        result = calculate_technical_factors(empty)
        assert isinstance(result, pd.DataFrame)


# ─── Factor Evaluation ───────────────────────────────────────────────


class TestFactorEval:
    def test_calc_forward_returns(self):
        close = pd.Series(
            [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110], dtype=float
        )
        fwd = calc_forward_returns(close, [1, 5])
        assert "fwd_1d" in fwd.columns
        assert "fwd_5d" in fwd.columns
        assert len(fwd) == len(close)

    def test_calc_ic(self):
        factor = pd.Series(np.random.randn(100))
        returns = pd.Series(np.random.randn(100))
        ic = calc_ic(factor, returns)
        assert -1 <= ic <= 1

    def test_calc_ic_insufficient_data(self):
        ic = calc_ic(pd.Series([1.0, 2.0]), pd.Series([0.1, 0.2]))
        assert np.isnan(ic)

    def test_factor_evaluation_report(self, cross_section):
        report = factor_evaluation_report(
            cross_section, ["MOMENTUM_1M", "VOLATILITY_20D"]
        )
        assert isinstance(report, pd.DataFrame)
        assert len(report) == 2
        assert "IR" in report.columns
        assert "IC_mean" in report.columns

    def test_factor_correlation_matrix(self, cross_section):
        corr = factor_correlation_matrix(
            cross_section, ["MOMENTUM_1M", "VOLATILITY_20D"]
        )
        assert isinstance(corr, pd.DataFrame)
        assert corr.shape == (2, 2)

    def test_quantile_returns(self, cross_section):
        qr = quantile_returns(cross_section, "MOMENTUM_1M", n_quantiles=5)
        assert isinstance(qr, pd.DataFrame)
        assert "mean" in qr.columns


# ─── Regime Detection ────────────────────────────────────────────────


class TestRegime:
    def test_calc_index_regime(self, index_daily):
        regime_df = calc_index_regime(index_daily)
        assert isinstance(regime_df, pd.DataFrame)
        assert "trend_regime" in regime_df.columns
        assert "vol_regime" in regime_df.columns
        assert set(regime_df["trend_regime"].unique()).issubset(
            {"bull", "neutral", "bear"}
        )
        assert set(regime_df["vol_regime"].unique()).issubset(
            {"low_vol", "normal_vol", "high_vol"}
        )

    def test_get_regime_on_date(self, index_daily):
        regime_df = calc_index_regime(index_daily)
        date_str = str(index_daily["date"].iloc[-1].date())
        regime = get_regime_on_date(regime_df, date_str)
        assert isinstance(regime, RegimeState)
        assert isinstance(regime.trend, TrendRegime)
        assert isinstance(regime.vol, VolRegime)

    def test_get_regime_missing_date(self, index_daily):
        regime_df = calc_index_regime(index_daily)
        regime = get_regime_on_date(regime_df, "19990101")
        assert regime.trend == TrendRegime.NEUTRAL
        assert regime.vol == VolRegime.NORMAL_VOL

    def test_should_trade_always(self):
        regime = RegimeState(trend=TrendRegime.BEAR, vol=VolRegime.HIGH_VOL)
        assert should_trade(regime, "always") is True

    def test_should_trade_trend_follow(self):
        assert should_trade(RegimeState(trend=TrendRegime.BULL), "trend_follow") is True
        assert (
            should_trade(RegimeState(trend=TrendRegime.NEUTRAL), "trend_follow") is True
        )
        assert (
            should_trade(RegimeState(trend=TrendRegime.BEAR), "trend_follow") is False
        )

    def test_should_trade_mean_reversion(self):
        assert (
            should_trade(RegimeState(trend=TrendRegime.NEUTRAL), "mean_reversion")
            is True
        )
        assert (
            should_trade(RegimeState(trend=TrendRegime.BULL), "mean_reversion") is True
        )
        assert (
            should_trade(
                RegimeState(trend=TrendRegime.BEAR, vol=VolRegime.HIGH_VOL),
                "mean_reversion",
            )
            is False
        )
        assert (
            should_trade(
                RegimeState(trend=TrendRegime.BEAR, vol=VolRegime.LOW_VOL),
                "mean_reversion",
            )
            is True
        )


# ─── Composite ───────────────────────────────────────────────────────


class TestComposite:
    def test_compute_ic_weights_equal(self, cross_section):
        weights = compute_ic_weights(
            cross_section, ["MOMENTUM_1M", "VOLATILITY_20D"], method="equal"
        )
        assert len(weights) == 2
        assert abs(sum(abs(w) for w in weights.values()) - 1.0) < 1e-6

    def test_compute_ic_weights_ir(self, cross_section):
        weights = compute_ic_weights(
            cross_section, ["MOMENTUM_1M", "VOLATILITY_20D"], method="ir"
        )
        assert len(weights) == 2
        assert abs(sum(abs(w) for w in weights.values()) - 1.0) < 1e-6

    def test_build_composite_panel(self, cross_section):
        panel, config = build_composite_panel(
            cross_section, ["MOMENTUM_1M", "VOLATILITY_20D"], weight_method="equal"
        )
        assert "composite_score" in panel.columns
        assert isinstance(config, CompositeConfig)

    def test_momentum_quality_composite(self, cross_section):
        panel, config = momentum_quality_composite(cross_section)
        assert "composite_score" in panel.columns
        assert set(config.factor_weights.keys()) == set(MOMENTUM_QUALITY_FACTORS)

    def test_mean_reversion_composite(self, cross_section):
        panel, config = mean_reversion_composite(cross_section)
        assert "composite_score" in panel.columns
        assert set(config.factor_weights.keys()) == set(MEAN_REVERSION_FACTORS)


# ─── Strategy ────────────────────────────────────────────────────────


class TestStrategy:
    def test_calc_position_sizes(self):
        df = pd.DataFrame({"VOLATILITY_20D": [0.1, 0.2, 0.3, 0.4, 0.5]})
        weights = calc_position_sizes(df)
        assert len(weights) == 5
        assert abs(weights.sum() - 1.0) < 1e-6
        # Lower vol should get higher weight
        assert weights.iloc[0] > weights.iloc[-1]

    def test_calc_position_sizes_empty(self):
        df = pd.DataFrame({"VOLATILITY_20D": []})
        weights = calc_position_sizes(df)
        assert len(weights) == 0

    def test_make_trend_follow_strategy(self):
        config, factors = make_trend_follow_strategy()
        assert isinstance(config, StrategyConfigAshare)
        assert config.strategy_mode == "trend_follow"
        assert len(factors) == 3

    def test_make_mean_reversion_strategy(self):
        config, factors = make_mean_reversion_strategy()
        assert isinstance(config, StrategyConfigAshare)
        assert config.strategy_mode == "mean_reversion"
        assert len(factors) == 3

    def test_strategy_performance_metrics(self):
        returns = pd.Series(
            [0.01, -0.005, 0.02, 0.003, -0.01, 0.015, 0.005, -0.002, 0.01, 0.008]
        )
        invested = pd.Series([True] * 10)
        metrics = strategy_performance_metrics(returns, invested)
        assert "sharpe" in metrics
        assert "max_drawdown" in metrics
        assert "win_rate" in metrics
        assert "calmar_ratio" in metrics
        assert "sortino_ratio" in metrics

    def test_strategy_performance_metrics_empty(self):
        metrics = strategy_performance_metrics(
            pd.Series(dtype=float), pd.Series(dtype=bool)
        )
        assert np.isnan(metrics["sharpe"])
        assert metrics["time_in_market"] == 0.0

    def test_run_strategy(self, cross_section, index_daily):
        regime_df = calc_index_regime(index_daily)
        config, _ = make_trend_follow_strategy()
        result = run_strategy(cross_section, regime_df, config, forward_col="fwd_1d")
        assert isinstance(result, pd.DataFrame)
        assert "portfolio_return" in result.columns
        assert "is_invested" in result.columns


class TestRegimeForwardFill:
    """Regime data gaps forward-fill the last known state; strategy semantics unchanged."""

    @staticmethod
    def _regime_df(dates, trend: str, vol: str = "normal_vol") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "date": d,
                    "trend_regime": trend,
                    "vol_regime": vol,
                    "index_close": 3000.0,
                    "index_ma20": 2950.0,
                    "index_ma60": 2900.0,
                    "realized_vol_20d": 0.15,
                }
                for d in dates
            ]
        )

    @staticmethod
    def _small_cross_section(dates) -> pd.DataFrame:
        np.random.seed(11)
        rows = []
        for d in dates:
            for i in range(6):
                rows.append(
                    {
                        "date": d,
                        "symbol": f"S{i:06d}",
                        "MOMENTUM_1M": np.random.randn(),
                        "VOLATILITY_20D": abs(np.random.randn()) * 0.3,
                        "MA_DEVIATION": np.random.randn() * 0.05,
                        "HIGH_52W_PROXIMITY": np.random.rand(),
                        "fwd_1d": np.random.randn() * 0.01,
                    }
                )
        panel = pd.DataFrame(rows)
        panel["composite_score"] = np.random.randn(len(panel))
        return panel

    @staticmethod
    def _config():
        config, _ = make_trend_follow_strategy(n_long=5)
        # small universe: disable screens designed for full-market sections
        return config.model_copy(
            update={
                "min_composite_percentile": 1.0,
                "require_price_above_ma20": False,
                "require_volume_above_avg": False,
            }
        )

    def test_regime_gap_forward_fills(self, caplog):
        dates = pd.date_range("2024-06-03", periods=3, freq="B")
        panel = self._small_cross_section(dates)
        regime_df = self._regime_df(dates[:2], trend="bull")  # date C missing

        with caplog.at_level(logging.WARNING):
            result = run_strategy(
                panel, regime_df, self._config(), forward_col="fwd_1d"
            )

        by_date = result.set_index("date")
        row_b = by_date.loc[dates[1]]
        row_c = by_date.loc[dates[2]]
        assert row_c["trend_regime"] == "bull"
        assert row_c["trend_regime"] != "unknown"
        assert bool(row_c["is_invested"]) == bool(row_b["is_invested"])
        assert bool(row_c["is_invested"]) is True
        assert "forward-filling from last known regime" in caplog.text

    def test_regime_missing_first_date_defaults_cash(self, caplog):
        dates = pd.date_range("2024-06-03", periods=2, freq="B")
        panel = self._small_cross_section(dates)
        regime_df = self._regime_df([], trend="bull")

        with caplog.at_level(logging.WARNING):
            result = run_strategy(
                panel, regime_df, self._config(), forward_col="fwd_1d"
            )

        assert result["is_invested"].eq(False).all()
        assert result["trend_regime"].eq("unknown").all()
        assert result["vol_regime"].eq("unknown").all()
        assert "no prior regime, defaulting to cash" in caplog.text

    def test_bear_regime_still_cashes(self):
        dates = pd.date_range("2024-06-03", periods=3, freq="B")
        panel = self._small_cross_section(dates)
        regime_df = self._regime_df(dates, trend="bear")

        result = run_strategy(panel, regime_df, self._config(), forward_col="fwd_1d")

        assert result["is_invested"].eq(False).all()
        assert result["trend_regime"].eq("bear").all()
        assert result["portfolio_return"].eq(0.0).all()


# ─── Backtest ────────────────────────────────────────────────────────


class TestBacktest:
    def test_construct_portfolio(self, cross_section):
        portfolio = construct_portfolio(cross_section, "MOMENTUM_1M", n_long=10)
        assert isinstance(portfolio, pd.DataFrame)
        assert "portfolio_return" in portfolio.columns

    def test_calc_performance_metrics(self):
        returns = pd.Series(np.random.randn(100) * 0.01)
        metrics = calc_performance_metrics(returns)
        assert "sharpe" in metrics
        assert "cumulative_return" in metrics
        assert "max_drawdown" in metrics

    def test_calc_performance_metrics_empty(self):
        metrics = calc_performance_metrics(pd.Series(dtype=float))
        assert np.isnan(metrics["sharpe"])

    def test_backtest_factor(self, cross_section):
        result = backtest_factor(cross_section, "MOMENTUM_1M", n_long=10)
        assert "metrics" in result
        assert "daily_returns" in result
        assert "sharpe" in result["metrics"]

    def test_backtest_factors_batch(self, cross_section):
        report = backtest_factors_batch(
            cross_section, ["MOMENTUM_1M", "VOLATILITY_20D"]
        )
        assert isinstance(report, pd.DataFrame)
        assert len(report) == 2
        assert "sharpe" in report.columns

    def test_backtest_composite(self, cross_section):
        config = CompositeConfig(
            name="test",
            factor_weights={"MOMENTUM_1M": 0.5, "VOLATILITY_20D": 0.5},
        )
        result = backtest_composite(cross_section, config, n_long=5)
        assert "metrics" in result

    def test_backtest_with_regime(self, cross_section, index_daily):
        regime_df = calc_index_regime(index_daily)
        result = backtest_with_regime(cross_section, regime_df, "MOMENTUM_1M")
        assert "metrics" in result
        assert "time_in_market" in result["metrics"]

    def test_backtest_full_strategy(self, cross_section, index_daily):
        regime_df = calc_index_regime(index_daily)
        config, _ = make_trend_follow_strategy()
        result = backtest_full_strategy(
            cross_section, regime_df, config, forward_col="fwd_1d"
        )
        assert "metrics" in result
        assert "daily_returns" in result


# ─── Sentiment ───────────────────────────────────────────────────────


class TestSentiment:
    def test_calc_limit_up_breadth(self):
        breadth = calc_limit_up_breadth({"limit_up_today": 50}, total_stocks=5000)
        assert abs(breadth - 0.01) < 1e-6

    def test_calc_limit_up_breadth_zero_stocks(self):
        breadth = calc_limit_up_breadth({"limit_up_today": 50}, total_stocks=0)
        assert np.isnan(breadth)

    def test_calc_seal_rate(self):
        rate = calc_seal_rate({"seal_rate": 0.75})
        assert rate == 0.75

    def test_calc_seal_rate_missing(self):
        rate = calc_seal_rate({})
        assert np.isnan(rate)

    def test_calc_limit_up_ratio(self):
        ratio = calc_limit_up_ratio({"limit_up_today": 30, "limit_down_today": 10})
        assert ratio == 3.0

    def test_calc_limit_up_ratio_no_down(self):
        ratio = calc_limit_up_ratio({"limit_up_today": 30, "limit_down_today": 0})
        assert ratio == 30

    def test_calc_max_consecutive_boards(self):
        df = pd.DataFrame({"consecutive_boards": [2, 5, 3]})
        assert calc_max_consecutive_boards(df) == 5

    def test_calc_max_consecutive_boards_empty(self):
        assert calc_max_consecutive_boards(pd.DataFrame()) == 0

    def test_calc_reason_cluster(self):
        df = pd.DataFrame({"reason_type": ["AI+芯片", "AI", "新能源", "AI"]})
        result = calc_reason_cluster(df, top_n=2)
        assert len(result) == 2
        assert result[0][0] == "AI"

    def test_calc_reason_cluster_empty(self):
        assert calc_reason_cluster(pd.DataFrame()) == []

    def test_calc_stock_limit_up_factor_in_pool(self):
        pool = pd.DataFrame(
            [
                {
                    "code": "000001",
                    "seal_amount": 1e8,
                    "float_market_cap": 5e9,
                    "open_count": 2,
                }
            ]
        )
        result = calc_stock_limit_up_factor("000001", pool)
        assert result["is_limit_up"] == 1
        assert abs(result["seal_strength"] - 0.02) < 1e-6

    def test_calc_stock_limit_up_factor_not_in_pool(self):
        pool = pd.DataFrame(
            columns=["code", "seal_amount", "float_market_cap", "open_count"]
        )
        result = calc_stock_limit_up_factor("000001", pool)
        assert result["is_limit_up"] == 0


# ─── Pydantic Models ─────────────────────────────────────────────────


class TestPydanticModels:
    def test_regime_state_defaults(self):
        r = RegimeState()
        assert r.trend == TrendRegime.NEUTRAL
        assert r.vol == VolRegime.NORMAL_VOL

    def test_composite_config(self):
        c = CompositeConfig(name="test", factor_weights={"A": 0.5, "B": 0.5})
        assert c.name == "test"
        assert c.direction == 1

    def test_strategy_config_ashare(self):
        cc = CompositeConfig(name="test", factor_weights={"A": 1.0})
        sc = StrategyConfigAshare(
            name="test_strat", composite_config=cc, strategy_mode="trend_follow"
        )
        assert sc.n_long == 5
        assert sc.stop_loss == -0.02
        assert sc.require_price_above_ma20 is True


# ─── Panel-level Factors ─────────────────────────────────────────────


class TestPanelFactors:
    def _make_panel(self, dates, symbols, closes_by_symbol):
        rows = []
        for d, date in enumerate(dates):
            for s in symbols:
                rows.append(
                    {
                        "date": date,
                        "symbol": s,
                        "close": closes_by_symbol[s][d],
                        "amount": 1e8 * (1 + (d % 5) / 5),
                        "turnover": 1.0 + (d % 7) / 7,
                    }
                )
        return pd.DataFrame(rows)

    def test_resid_vol_less_than_raw_vol(self):
        from selene_space_ashare.panel_factors import compute_resid_vol_60d

        np.random.seed(11)
        n = 120
        dates = [
            d.strftime("%Y%m%d")
            for d in pd.date_range("2024-01-01", periods=n, freq="B")
        ]
        idx_close = 3000 + np.cumsum(np.random.randn(n) * 10)
        index_daily = pd.DataFrame({"date": dates, "close": idx_close})

        # stock tracks the index with tiny noise -> residual vol far below
        # the stock's own raw return vol (R^2 ~ 1 construction)
        noise = np.random.randn(n) * 0.0002
        stk_close = idx_close * (1 + noise)
        panel = pd.DataFrame({"date": dates, "symbol": "000001", "close": stk_close})

        rv = compute_resid_vol_60d(panel, index_daily)
        valid = rv.dropna()
        assert len(valid) > 0
        stk_ret = pd.Series(stk_close).pct_change()
        raw_vol = float(stk_ret.std() * np.sqrt(252))
        assert (valid < raw_vol).all()

    def test_resid_vol_date_misalignment(self):
        from selene_space_ashare.panel_factors import compute_resid_vol_60d

        np.random.seed(12)
        n = 120
        dates = [
            d.strftime("%Y%m%d")
            for d in pd.date_range("2024-01-01", periods=n, freq="B")
        ]
        idx_close = 3000 + np.cumsum(np.random.randn(n) * 10)
        index_daily = pd.DataFrame({"date": dates, "close": idx_close})

        # stock dates: drop every 5th day (misaligned vs index)
        stk_dates = [d for i, d in enumerate(dates) if i % 5 != 0]
        stk_close = 10 + np.cumsum(np.random.randn(len(stk_dates)) * 0.1)
        panel = pd.DataFrame(
            {"date": stk_dates, "symbol": "000001", "close": stk_close}
        )

        rv = compute_resid_vol_60d(panel, index_daily)
        assert len(rv) == len(panel)  # aligned back to panel length
        assert rv.isna().all() or len(rv.dropna()) > 0  # no exception raised

    def test_crowding_rank_correlation(self):
        from scipy.stats import spearmanr

        from selene_space_ashare.panel_factors import compute_crowding_score

        np.random.seed(13)
        dates = ["20240102", "20240103", "20240104"]
        symbols = [f"S{i:02d}" for i in range(10)]
        # turnover proportional to amount: both components co-move, so the
        # product preserves (and amplifies) the amount ordering
        rows = []
        for d in dates:
            for s in symbols:
                amt = np.random.rand() * 1e9
                rows.append(
                    {
                        "date": d,
                        "symbol": s,
                        "amount": amt,
                        "turnover": amt / 1e8,
                    }
                )
        panel = pd.DataFrame(rows)

        cs = compute_crowding_score(panel)
        valid = cs.notna()
        assert valid.all()
        corr, _ = spearmanr(panel.loc[valid, "amount"], cs[valid])
        assert corr > 0.5

    def test_crowding_missing_columns_nan(self, caplog):
        from selene_space_ashare.panel_factors import compute_crowding_score

        panel = pd.DataFrame(
            {
                "date": ["20240102"] * 3,
                "symbol": ["A", "B", "C"],
                "close": [10.0, 11.0, 12.0],
            }
        )
        with caplog.at_level(logging.WARNING):
            cs = compute_crowding_score(panel)
        assert cs.isna().all()
        assert "missing panel columns" in caplog.text

    def test_panel_factors_registered(self):
        from selene_space_types.stock import FactorCategory

        from selene_space_ashare.factors import ALL_FACTORS

        for name in ("RESID_VOL_60D", "CROWDING_SCORE"):
            assert name in ALL_FACTORS
            f = ALL_FACTORS[name]
            assert f.category == FactorCategory.TECHNICAL
            assert f.direction == -1
        assert ALL_FACTORS["RESID_VOL_60D"].subcategory == "volatility"
        assert ALL_FACTORS["CROWDING_SCORE"].subcategory == "volume_price"


# ─── Fundamental Factors (US-B2) ─────────────────────────────────────


class TestFundamentalFactors:
    def test_ep_bp_yoy_registered(self):
        for name in ("EP", "BP", "YOY_NI", "ROE"):
            assert name in ALL_FACTORS
        assert list(ALL_FACTORS).count("ROE") == 1

    def test_ep_bp_listed_as_fundamental_value(self):
        fund = get_fundamental_factors()
        assert "EP" in fund
        assert "BP" in fund
        assert "YOY_NI" in fund
        assert fund["EP"].subcategory == "value"
        assert fund["BP"].subcategory == "value"
        assert fund["YOY_NI"].subcategory == "growth"

    def test_direction_positive(self):
        for name in ("EP", "BP", "YOY_NI"):
            assert ALL_FACTORS[name].direction == 1

    def test_pit_no_lookahead_real_df(self):
        daily = pd.DataFrame(
            {
                "symbol": ["600519"] * 4,
                "date": ["20240301", "20240315", "20240425", "20240430"],
                "close": [1700.0, 1720.0, 1680.0, 1690.0],
            }
        )
        # Q1 report only announced on 2024-04-25 — dates before that must see NaN
        financials = [
            {
                "symbol": "600519",
                "pubDate": "2024-04-25",
                "statDate": "2024-03-31",
                "epsTTM": 60.0,
                "YOYNI": 0.15,
                "assetEquity": 0.75,
                "roeAvg": 0.08,
            }
        ]
        out = attach_fundamentals(daily, financials)

        before = out[out["date"] == "20240301"]
        assert before["epsTTM"].isna().all()
        assert before["YOYNI"].isna().all()
        assert before["assetEquity"].isna().all()

        mid = out[out["date"] == "20240315"]
        assert mid["epsTTM"].isna().all()

        after = out[out["date"] == "20240425"]
        assert after["epsTTM"].item() == pytest.approx(60.0)
        assert after["YOYNI"].item() == pytest.approx(0.15)
        assert after["assetEquity"].item() == pytest.approx(0.75)
        assert after["roeAvg"].item() == pytest.approx(0.08)

        last = out[out["date"] == "20240430"]
        assert last["epsTTM"].item() == pytest.approx(60.0)

    def test_pit_empty_financials_all_nan(self):
        daily = pd.DataFrame(
            {
                "symbol": ["600519"],
                "date": ["20240301"],
                "close": [1700.0],
            }
        )
        out = attach_fundamentals(daily, [])
        assert out["epsTTM"].isna().all()
        assert len(out) == 1


# ─── HS300 PIT History Slot (US-B2 / AC-B5) ──────────────────────────


class TestHs300History:
    @pytest.fixture
    def history_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "symbol": ["600519", "600036"],
                "in_date": ["2018-01-01", "2018-01-01"],
                "out_date": [pd.NaT, "2023-06-30"],
            }
        )

    def test_hs300_history_slot(self, tmp_path, monkeypatch, history_df):
        import selene_space_ashare.data_fetcher as df_mod

        monkeypatch.setattr(df_mod, "CACHE_DIR", tmp_path)
        out_dir = tmp_path / "universe"
        out_dir.mkdir()
        history_df.to_parquet(out_dir / "hs300_history.parquet", index=False)

        got = get_hs300_history()
        assert got is not None
        assert {"symbol", "in_date", "out_date"}.issubset(got.columns)
        assert set(got["symbol"]) == {"600519", "600036"}

    def test_hs300_history_missing_returns_none(self, tmp_path, monkeypatch):
        import selene_space_ashare.data_fetcher as df_mod

        monkeypatch.setattr(df_mod, "CACHE_DIR", tmp_path)
        assert get_hs300_history() is None

    def test_filter_universe_by_history(self, history_df):
        # After 600036 out_date: only permanent member remains
        assert filter_universe_by_history(history_df, "2023-12-31") == {"600519"}
        # Inside both membership windows: both present
        assert filter_universe_by_history(history_df, "2020-06-30") == {
            "600519",
            "600036",
        }
        # Boundary: out_date is exclusive — 600036 dropped on its out date
        assert filter_universe_by_history(history_df, "2023-06-30") == {"600519"}


# ─── batch9: CSI 500 constituents (US-1) ────────────────────────────


class TestCsi500Constituents:
    def test_cleans_dirty_rows(self, tmp_path, monkeypatch):
        import selene_space_ashare.data_fetcher as df_mod

        monkeypatch.setattr(df_mod, "CACHE_DIR", tmp_path)
        raw = pd.DataFrame(
            {
                "成分券代码": ["000905", "600519", "000905X", None],
                "成分券名称": ["中证500", "贵州茅台", "dirty", "bad"],
            }
        )
        monkeypatch.setattr(df_mod, "_fetch_with_retry", lambda func, **kw: raw)
        monkeypatch.setattr(df_mod, "_write_cache", lambda df, name, subdir: None)

        got = get_csi500_constituents(force=True)
        assert list(got.columns) == ["code", "name"]
        assert len(got) == 2
        assert set(got["code"].astype(str)) == {"000905", "600519"}
        assert got["code"].astype(str).str.match(r"^\d{6}$").all()

    def test_cache_hit_skips_network(self, tmp_path, monkeypatch):
        import selene_space_ashare.data_fetcher as df_mod

        monkeypatch.setattr(df_mod, "CACHE_DIR", tmp_path)
        universe = tmp_path / "universe"
        universe.mkdir()
        cache_file = universe / "csi500.parquet"
        cached = pd.DataFrame({"code": ["000905", "600519"], "name": ["a", "b"]})
        cached.to_parquet(cache_file, index=False)
        monkeypatch.setattr(df_mod, "_read_cache", lambda name, subdir: cached)

        def _boom(func, **kw):
            raise AssertionError("network fetch must not fire on cache hit")

        monkeypatch.setattr(df_mod, "_fetch_with_retry", _boom)

        got = get_csi500_constituents()
        assert len(got) == 2
        assert set(got["code"].astype(str)) == {"000905", "600519"}

    def test_fetch_failure_returns_empty(self, tmp_path, monkeypatch):
        import selene_space_ashare.data_fetcher as df_mod

        monkeypatch.setattr(df_mod, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(df_mod, "_read_cache", lambda name, subdir: None)
        monkeypatch.setattr(df_mod, "_fetch_with_retry", lambda func, **kw: None)

        got = get_csi500_constituents(force=True)
        assert got.empty
        assert list(got.columns) == ["code", "name"]


# ─── batch9: THS full-history kline cache (US-1 follow-up) ──────────


class TestFetchKlinesThsAll:
    def test_full_cache_hit_skips_year_loop(self, tmp_path, monkeypatch):
        import selene_space_ashare.ths_data as ths

        monkeypatch.setattr(ths, "CACHE_DIR", tmp_path)
        subdir = tmp_path / "ths_klines" / "daily"
        subdir.mkdir(parents=True)
        cached = pd.DataFrame({"date": ["20250102", "20250103"], "close": [1.0, 1.1]})
        cached.to_parquet(subdir / "000001_full.parquet", index=False)

        def _boom(*args, **kw):
            raise AssertionError("year loop must not fire on _full cache hit")

        monkeypatch.setattr(ths, "fetch_klines_ths", _boom)

        got = ths.fetch_klines_ths_all("000001")
        assert len(got) == 2

    def test_year_loop_uses_year_range_files(self, tmp_path, monkeypatch):
        import selene_space_ashare.ths_data as ths

        monkeypatch.setattr(ths, "CACHE_DIR", tmp_path)
        seen: list[str] = []

        class FakeResp:
            def raise_for_status(self):
                pass

            text = 'cb({"data":"20260105,10.0,10.5,9.9,10.2,100,1000.0,0.5"});'

        def fake_get(url, **kw):
            seen.append(url)
            return FakeResp()

        fake_requests = type(sys)("requests")
        fake_requests.get = fake_get
        fake_requests.RequestException = Exception
        monkeypatch.setitem(sys.modules, "requests", fake_requests)

        monkeypatch.setattr(ths.time, "sleep", lambda s: None)

        got = ths.fetch_klines_ths_all("000001", force=True)
        year_urls = [u for u in seen if u.endswith(".js") and "/01/" in u]
        assert any(u.endswith("2026.js") for u in year_urls), seen
        assert any(u.endswith("2025.js") for u in year_urls), seen
        assert not any(u.endswith("last.js") for u in year_urls), seen
        assert got["date"].tolist() == ["20260105"]
        assert (tmp_path / "ths_klines" / "daily" / "000001_full.parquet").exists()


# ─── batch4: SIZE derivation ────────────────────────────────────────


class TestDeriveSizeFactor:
    def test_moutai_anchor(self):
        # 茅台 2024Q4 实测: volume=3098122 股, turnover=0.248% → 流通股本 ≈12.49 亿股
        # (真值 12.56 亿, batch4 plan 三锚点实证)
        import math

        from selene_space_ashare.factor_calc import derive_size_factor

        df = pd.DataFrame(
            {"volume": [3098122.0], "turnover": [0.248], "close": [1400.0]}
        )
        fs = 3098122.0 * 100.0 / 0.248
        assert abs(fs / 1e8 - 12.49) < 0.5
        got = derive_size_factor(df)
        assert abs(float(got.iloc[0]) - math.log(fs * 1400.0)) < 1e-9

    def test_turnover_invalid_rows_nan(self):
        from selene_space_ashare.factor_calc import derive_size_factor

        df = pd.DataFrame(
            {
                "volume": [1e6, 1e6, 1e6],
                "turnover": [0.0, float("nan"), None],
                "close": [10.0, 10.0, 10.0],
            }
        )
        got = derive_size_factor(df)
        assert got.isna().all()

    def test_size_ordering(self):
        # Same turnover, higher price → bigger mktcap → bigger SIZE (ln monotone)
        from selene_space_ashare.factor_calc import derive_size_factor

        df = pd.DataFrame(
            {"volume": [1e7, 1e7], "turnover": [1.0, 1.0], "close": [10.0, 100.0]}
        )
        got = derive_size_factor(df)
        assert got.iloc[1] > got.iloc[0]

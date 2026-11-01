"""Metrics computation: drawdown, aggregate stats, interval annualization."""

import json

import numpy as np
import pytest

from selene_space_backtest.metrics import (
    _max_drawdown,
    compute_metrics,
    infer_annualization_factor,
)


class TestDrawdownMath:
    def test_monotonic_growth_has_zero_drawdown(self) -> None:
        assert _max_drawdown(np.array([100.0, 110.0, 120.0, 130.0])) == 0.0

    def test_peak_to_trough_fraction(self) -> None:
        # trough 90 off peak 120 → (90 - 120) / 120 = -0.25
        assert _max_drawdown(np.array([100.0, 120.0, 90.0, 110.0])) == pytest.approx(
            -0.25
        )

    def test_pure_decline(self) -> None:
        # (60 - 100) / 100 = -0.40
        assert _max_drawdown(np.array([100.0, 80.0, 60.0])) == pytest.approx(-0.40)


class TestAggregates:
    def test_blank_inputs_yield_zeroed_metrics(self) -> None:
        m = compute_metrics([], [])
        assert m.total_return == 0.0
        assert m.total_trades == 0

    def test_flat_single_point_is_zero_return(self) -> None:
        m = compute_metrics([10000.0], [])
        assert m.total_return == 0.0

    def test_mixed_trades_produce_full_stats(self) -> None:
        m = compute_metrics(
            [10000.0, 10500.0, 11000.0, 11500.0],
            [{"pnl": 500.0}, {"pnl": -200.0}, {"pnl": 300.0}],
        )

        assert m.total_return == pytest.approx(0.15, abs=1e-4)
        assert m.max_drawdown == 0.0
        assert m.win_rate == pytest.approx(2 / 3, abs=1e-4)
        assert m.total_trades == 3
        assert m.profit_factor == pytest.approx(800.0 / 200.0, abs=1e-4)

    def test_steady_gains_score_positive_sharpe(self) -> None:
        m = compute_metrics(
            [10000.0, 10100.0, 10200.0, 10300.0, 10400.0],
            [],
            annualization_factor=252,
        )
        assert m.sharpe_ratio > 0

    def test_lossless_runs_clamp_profit_factor_for_serialization(self) -> None:
        # Clamp at 1e9 (not inf) so JSON and downstream float math survive.
        m = compute_metrics(
            [10000.0, 10500.0, 11000.0],
            [{"pnl": 500.0}, {"pnl": 500.0}],
        )
        assert m.profit_factor == 1e9
        assert m.had_no_losses is True

    def test_losing_trade_restores_true_profit_factor(self) -> None:
        m = compute_metrics(
            [10000.0, 10500.0, 11000.0],
            [{"pnl": 500.0}, {"pnl": -100.0}, {"pnl": 300.0}],
        )
        assert m.profit_factor == pytest.approx(800.0 / 100.0, abs=1e-4)
        assert m.had_no_losses is False

    def test_clamped_value_survives_json_round_trip(self) -> None:
        m = compute_metrics([10000.0, 10500.0], [{"pnl": 500.0}])
        recovered = json.loads(json.dumps(m.model_dump()))
        assert recovered["profit_factor"] == 1e9
        assert recovered["had_no_losses"] is True

    def test_without_trades_win_rate_and_pf_are_zero(self) -> None:
        m = compute_metrics([10000.0, 10000.0, 10000.0], [])
        assert m.win_rate == 0.0
        assert m.profit_factor == 0.0
        assert m.total_trades == 0


class TestAnnualReturnCap:
    """年化收益封顶(QD 同款 1e4 倍):短窗口爆仓样本不得产出 inf 炸 JSON。"""

    def test_explosive_growth_is_capped_at_10k(self) -> None:
        # 10000x total return over 2 periods annualizes past any float → cap.
        m = compute_metrics([100.0, 100000.0, 1_000_000_000.0], [])
        assert m.annual_return == 10_000.0
        assert np.isfinite(m.annual_return)

    def test_capped_value_survives_json_round_trip(self) -> None:
        m = compute_metrics([100.0, 100000.0, 1_000_000_000.0], [])
        recovered = json.loads(json.dumps(m.model_dump()))
        assert recovered["annual_return"] == 10_000.0

    def test_capped_flag_set_when_truncated(self) -> None:
        """封顶时 annual_return_capped=True —— UI 据此提示,否则是静默改数。"""
        m = compute_metrics([100.0, 100000.0, 1_000_000_000.0], [])
        assert m.annual_return_capped is True

    def test_capped_flag_clear_when_not_truncated(self) -> None:
        eq = [10000.0 * (1.10 ** (i / 365)) for i in range(366)]
        m = compute_metrics(eq, [])
        assert m.annual_return_capped is False

    def test_capped_flag_survives_json_round_trip(self) -> None:
        """标记必须随 API 到达前端,否则等于没加。"""
        m = compute_metrics([100.0, 100000.0, 1_000_000_000.0], [])
        recovered = json.loads(json.dumps(m.model_dump()))
        assert recovered["annual_return_capped"] is True

    def test_modest_growth_is_uncapped(self) -> None:
        # 10% over 365 daily bars → ~10% annualized, far below the cap.
        eq = [10000.0 * (1.10 ** (i / 365)) for i in range(366)]
        m = compute_metrics(eq, [])
        assert 0.0 < m.annual_return < 10_000.0

    def test_total_wipeout_stays_at_minus_one(self) -> None:
        m = compute_metrics([10000.0, 0.0], [])
        assert m.annual_return == -1.0


class TestIntervalToAnnualization:
    """Annualization factor adapts to the bar interval."""

    def test_hourly_bars(self) -> None:
        assert infer_annualization_factor("1h") == 8760

    def test_four_hour_bars(self) -> None:
        assert infer_annualization_factor("4h") == 2190

    def test_daily_bars(self) -> None:
        assert infer_annualization_factor("1d") == 365

    def test_minute_bars(self) -> None:
        assert infer_annualization_factor("1m") == 525_600

    def test_quarter_hour_bars(self) -> None:
        assert infer_annualization_factor("15m") == 35_040

    def test_weekly_bars(self) -> None:
        assert infer_annualization_factor("1w") == 52

    def test_unrecognized_interval_falls_back_to_daily(self) -> None:
        assert infer_annualization_factor("3d") == 365

    def test_lookup_ignores_case(self) -> None:
        assert infer_annualization_factor("1H") == 8760

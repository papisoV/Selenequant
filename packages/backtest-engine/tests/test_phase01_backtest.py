"""Tests for Phase 0+1 backtest credibility improvements.

Verifies: default commission/slippage, next-bar-open fill, risk-based sizing,
no look-ahead bias.
"""

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
for _p in [
    _PROJECT_ROOT / "packages" / "core",
    _PROJECT_ROOT / "packages" / "shared-types",
    _PROJECT_ROOT / "packages" / "backtest-engine",
]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from selene_space_backtest.commissions import PercentageCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.slippage import RealisticSlippage


class TestDefaults:
    """BacktestConfig defaults should use realistic costs."""

    def test_default_commission_is_percentage(self):
        c = BacktestConfig()
        assert isinstance(c.commission, PercentageCommission)

    def test_default_slippage_is_realistic(self):
        # P2-1: default slippage is RealisticSlippage — the same algorithm
        # used by paper/live fills (3bp base + volume-impact term). This
        # closes the backtest↔paper slippage divergence. The previous
        # CompositeSlippage(VolumeSlippage, BidAskSpreadModel) default was
        # split into two concerns: RealisticSlippage for market impact,
        # and BidAskSpreadModel is available as an explicit opt-in via
        # CompositeSlippage when per-symbol spread modeling is needed.
        c = BacktestConfig()
        assert isinstance(c.slippage, RealisticSlippage)

    def test_default_commission_rate(self):
        c = BacktestConfig()
        assert c.commission._rate == pytest.approx(0.001)

    def test_default_slippage_has_volume_impact(self):
        # RealisticSlippage carries both a base spread (3bp) and a volume
        # participation term — the two concerns the previous CompositeSlippage
        # default bundled. Verified by checking the impact factor is non-zero
        # and the base fraction matches paper's 3bp default.
        c = BacktestConfig()
        assert isinstance(c.slippage, RealisticSlippage)
        assert c.slippage._impact_factor == pytest.approx(0.1)
        assert c.slippage._base_fraction == pytest.approx(3.0 / 10_000.0)

    def test_no_trade_on_close_field(self):
        c = BacktestConfig()
        assert not hasattr(c, "trade_on_close")


class TestRiskSizing:
    """Position sizing should use risk-based formula by default."""

    def test_risk_based_is_default(self):
        c = BacktestConfig()
        assert c.position_sizing == "risk_based"

    def test_risk_per_trade_default(self):
        c = BacktestConfig()
        assert c.risk_per_trade == pytest.approx(0.01)

    def test_stop_loss_atr_mult_default(self):
        c = BacktestConfig()
        assert c.stop_loss_atr_mult == pytest.approx(2.0)

    def test_fixed_mode_available(self):
        c = BacktestConfig(position_sizing="fixed")
        assert c.position_sizing == "fixed"


class TestOverfitGate:
    """Overfit gate should block strategies with high PBO or low DSR."""

    def test_pass_low_pbo(self):
        report = {"pbo": 0.3, "deflated_sharpe_ratio": 0.95, "passed": True, "strategy_id": "ok"}
        assert BacktestEngine.check_overfit_gate(report) is True

    def test_block_high_pbo(self):
        report = {"pbo": 0.7, "deflated_sharpe_ratio": 0.95, "passed": True, "strategy_id": "bad"}
        assert BacktestEngine.check_overfit_gate(report) is False

    def test_block_low_dsr(self):
        report = {"pbo": 0.2, "deflated_sharpe_ratio": 0.2, "passed": True, "strategy_id": "bad"}
        assert BacktestEngine.check_overfit_gate(report) is False

    def test_block_not_passed(self):
        report = {"pbo": 0.2, "deflated_sharpe_ratio": 0.95, "passed": False, "strategy_id": "bad"}
        assert BacktestEngine.check_overfit_gate(report) is False

"""Commission model contract: zero model and percentage model."""

import pytest

from selene_space_backtest.commissions import NoCommission, PercentageCommission


class TestZeroCommission:
    def test_fee_is_always_zero(self) -> None:
        model = NoCommission()
        assert model.calculate(100.0, 1.0) == 0.0


class TestPercentFee:
    def test_factory_default_is_ten_bps(self) -> None:
        model = PercentageCommission()  # 0.1%
        assert model.calculate(10000.0, 1.0) == pytest.approx(10.0)

    def test_explicit_rate_multiplies_notional(self) -> None:
        model = PercentageCommission(rate=0.002)  # 0.2%
        assert model.calculate(50000.0, 0.5) == pytest.approx(50.0)

    def test_rejects_negative_rate(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            PercentageCommission(rate=-0.01)

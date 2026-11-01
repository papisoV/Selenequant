"""Slippage model contract: pass-through model and fixed-bps model."""

import pytest

from selene_space_backtest.slippage import FixedSlippage, NoSlippage
from selene_space_types.trading import OrderSide


class TestPassThrough:
    def test_buy_side_untouched(self) -> None:
        model = NoSlippage()
        assert model.apply(100.0, OrderSide.BUY) == 100.0

    def test_sell_side_untouched(self) -> None:
        model = NoSlippage()
        assert model.apply(100.0, OrderSide.SELL) == 100.0


class TestFixedBasisPoints:
    def test_buy_fills_above_quote(self) -> None:
        model = FixedSlippage(bps=10)  # 0.10%
        assert model.apply(1000.0, OrderSide.BUY) == 1000.0 * 1.001

    def test_sell_fills_below_quote(self) -> None:
        model = FixedSlippage(bps=10)
        assert model.apply(1000.0, OrderSide.SELL) == 1000.0 * 0.999

    def test_constructor_default_is_one_bp(self) -> None:
        model = FixedSlippage()
        assert model.apply(10000.0, OrderSide.BUY) == 10000.0 * 1.0001

    def test_rejects_negative_bps(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            FixedSlippage(bps=-1)

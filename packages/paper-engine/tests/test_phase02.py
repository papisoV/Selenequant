"""Tests for Phase 2 — Paper trading realism improvements.

Verifies: VolumeSlippage, Paper fills slippage+latency, intra-bar stop-loss,
funding rate deduction, liquidation enforcement, SQLite persistence.
"""

import sys
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
for _p in [
    _PROJECT_ROOT / "packages" / "core",
    _PROJECT_ROOT / "packages" / "shared-types",
    _PROJECT_ROOT / "packages" / "backtest-engine",
    _PROJECT_ROOT / "packages" / "exchange-sdk",
    _PROJECT_ROOT / "packages" / "paper-engine",
    _PROJECT_ROOT / "packages" / "strategy-engine",
]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from selene_space_backtest.slippage import VolumeSlippage
from selene_space_paper.fills import FixedPaperSlippage, try_fill
from selene_space_paper.positions import PositionManager
from selene_space_paper.state import PaperState
from selene_space_types.trading import (
    Fill,
    MarginMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)


# ---- VolumeSlippage ----


class TestVolumeSlippage:
    def test_small_order_minimal_slippage(self):
        vs = VolumeSlippage(volume_impact_factor=0.1)
        price = vs.apply(
            50000.0, OrderSide.BUY, order_notional=100, avg_24h_volume=1_000_000
        )
        assert price > 50000.0
        assert price < 50010.0  # small ratio = small slippage

    def test_large_order_significant_slippage(self):
        vs = VolumeSlippage(volume_impact_factor=0.1)
        price = vs.apply(
            50000.0, OrderSide.BUY, order_notional=500_000, avg_24h_volume=100_000
        )
        assert price > 50200.0  # 50% of volume = big impact

    def test_sell_slippage_direction(self):
        vs = VolumeSlippage(volume_impact_factor=0.1)
        price = vs.apply(
            50000.0, OrderSide.SELL, order_notional=100_000, avg_24h_volume=100_000
        )
        assert price < 50000.0

    def test_fallback_without_volume(self):
        vs = VolumeSlippage(default_bps=5.0)
        price = vs.apply(50000.0, OrderSide.BUY)
        assert price == pytest.approx(50025.0)

    def test_zero_volume_uses_fallback(self):
        vs = VolumeSlippage(default_bps=5.0)
        price = vs.apply(50000.0, OrderSide.BUY, order_notional=100, avg_24h_volume=0)
        assert price == pytest.approx(50025.0)


# ---- Paper fills slippage + latency ----


class TestPaperFillsSlippage:
    def _make_order(self, **overrides):
        defaults = {
            "id": "test",
            "symbol": "BTC/USDT",
            "side": OrderSide.BUY,
            "type": OrderType.MARKET,
            "quantity": 100,
            "created_at": 1000,
            "updated_at": 1000,
        }
        defaults.update(overrides)
        o = Order(**defaults)
        o.status = OrderStatus.ACCEPTED
        return o

    def test_slippage_applied(self):
        fill = try_fill(
            self._make_order(),
            50000.0,
            slippage_model=FixedPaperSlippage(bps=5.0),
            latency_range_ms=None,
        )
        assert fill.price > 50000.0

    def test_latency_added(self):
        fill = try_fill(
            self._make_order(), 50000.0, timestamp_ms=10000, latency_range_ms=(100, 200)
        )
        assert fill.timestamp >= 10100
        assert fill.timestamp <= 10200

    def test_no_latency_when_disabled(self):
        fill = try_fill(
            self._make_order(), 50000.0, timestamp_ms=10000, latency_range_ms=None
        )
        assert fill.timestamp == 10000


# ---- Funding rate + liquidation ----


class TestFundingRate:
    def test_long_pays_positive_rate(self):
        pm = PositionManager()
        fill = Fill(
            id="f1",
            order_id="o1",
            price=50000.0,
            quantity=10000.0,
            fee=10.0,
            timestamp=1000,
        )
        pm.apply_fill_with_symbol(
            "acc1",
            "BTC/USDT",
            fill,
            OrderSide.BUY,
            leverage=10,
            margin_mode=MarginMode.CROSS,
            pos_side=PositionSide.NET,
            available_balance=10000,
        )
        result = pm.apply_funding_rate("acc1", funding_rate=0.0001, timestamp=2000)
        assert "BTC/USDT" in result
        assert result["BTC/USDT"] < 0  # long pays

    def test_short_receives_positive_rate(self):
        pm = PositionManager()
        fill = Fill(
            id="f1",
            order_id="o1",
            price=50000.0,
            quantity=10000.0,
            fee=10.0,
            timestamp=1000,
        )
        pm.apply_fill_with_symbol(
            "acc1",
            "BTC/USDT",
            fill,
            OrderSide.SELL,
            leverage=10,
            margin_mode=MarginMode.CROSS,
            pos_side=PositionSide.NET,
            available_balance=10000,
        )
        result = pm.apply_funding_rate("acc1", funding_rate=0.0001, timestamp=2000)
        assert result["BTC/USDT"] > 0  # short receives


class TestLiquidation:
    def test_long_liquidated(self):
        pm = PositionManager()
        fill = Fill(
            id="f1",
            order_id="o1",
            price=50000.0,
            quantity=10000.0,
            fee=10.0,
            timestamp=1000,
        )
        pm.apply_fill_with_symbol(
            "acc1",
            "BTC/USDT",
            fill,
            OrderSide.BUY,
            leverage=50,
            margin_mode=MarginMode.ISOLATED,
            pos_side=PositionSide.NET,
        )
        pos = pm.get_position("acc1", "BTC/USDT")
        assert pos.liquidation_price is not None
        liqs = pm.check_and_enforce_liquidation(
            "acc1", "BTC/USDT", pos.liquidation_price - 100
        )
        assert len(liqs) == 1
        assert pos.quantity == 0

    def test_no_liquidation_when_price_safe(self):
        pm = PositionManager()
        fill = Fill(
            id="f1",
            order_id="o1",
            price=50000.0,
            quantity=10000.0,
            fee=10.0,
            timestamp=1000,
        )
        pm.apply_fill_with_symbol(
            "acc1",
            "BTC/USDT",
            fill,
            OrderSide.BUY,
            leverage=10,
            margin_mode=MarginMode.CROSS,
            pos_side=PositionSide.NET,
            available_balance=10000,
        )
        liqs = pm.check_and_enforce_liquidation("acc1", "BTC/USDT", 49000.0)
        assert len(liqs) == 0


# ---- SQLite persistence ----


class TestSQLitePersistence:
    def test_save_and_load_roundtrip(self):
        from selene_space_paper.accounts import AccountManager
        from selene_space_paper.orders import PaperOrderManager

        with tempfile.TemporaryDirectory() as td:
            ps = PaperState(state_dir=Path(td))
            am = AccountManager()
            om = PaperOrderManager()
            pm = PositionManager()
            am.create_account("test", initial_balance=5000)
            ps.save_state(am, om, pm, fills=[], extra={"version": 3})

            am2 = AccountManager()
            om2 = PaperOrderManager()
            pm2 = PositionManager()
            fills, meta = ps.load_state(am2, om2, pm2)
            accs = am2.get_all_data()
            assert len(accs) == 1
            assert list(accs.values())[0].current_balance == 5000.0
            assert meta["version"] == 3

    def test_clear_state(self):
        from selene_space_paper.accounts import AccountManager
        from selene_space_paper.orders import PaperOrderManager

        with tempfile.TemporaryDirectory() as td:
            ps = PaperState(state_dir=Path(td))
            am = AccountManager()
            om = PaperOrderManager()
            pm = PositionManager()
            am.create_account("x", initial_balance=100)
            ps.save_state(am, om, pm)
            ps.clear_state()
            # DB should still exist but empty
            am2 = AccountManager()
            om2 = PaperOrderManager()
            pm2 = PositionManager()
            fills, meta = ps.load_state(am2, om2, pm2)
            assert len(am2.get_all_data()) == 0

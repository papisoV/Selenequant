"""Tests for execution quality analysis.

Covers:
  - Implementation shortfall (arrival price vs fill)
  - VWAP benchmark comparison
  - Slippage report aggregation
"""

from __future__ import annotations

import pytest

from selene_space_exchange.execution.quality.metrics import (
    SlippageRecord,
    aggregate_slippage_report,
    compute_arrival_slippage_bps,
    compute_implementation_shortfall,
    compute_vwap,
    compute_vwap_benchmark,
    group_slippage_by_side,
    group_slippage_by_symbol,
)


# ---------------------------------------------------------------------------
# Implementation shortfall
# ---------------------------------------------------------------------------


class TestImplementationShortfall:
    def test_buy_above_arrival_positive_slippage(self) -> None:
        """Buy at higher price = positive shortfall (cost)."""
        result = compute_implementation_shortfall(
            arrival_price=100.0, fill_price=100.50, quantity=10.0, side="buy",
        )
        assert result["shortfall_bps"] == pytest.approx(50.0)  # 0.5%
        assert result["shortfall_notional"] == pytest.approx(5.0)

    def test_buy_below_arrival_negative_slippage(self) -> None:
        """Buy at lower price = negative shortfall (saving)."""
        result = compute_implementation_shortfall(
            arrival_price=100.0, fill_price=99.50, quantity=10.0, side="buy",
        )
        assert result["shortfall_bps"] == pytest.approx(-50.0)

    def test_sell_below_arrival_positive_slippage(self) -> None:
        """Sell at lower price = positive shortfall (cost)."""
        result = compute_implementation_shortfall(
            arrival_price=100.0, fill_price=99.00, quantity=10.0, side="sell",
        )
        assert result["shortfall_bps"] == pytest.approx(100.0)

    def test_sell_above_arrival_negative_slippage(self) -> None:
        """Sell at higher price = negative shortfall (saving)."""
        result = compute_implementation_shortfall(
            arrival_price=100.0, fill_price=101.00, quantity=10.0, side="sell",
        )
        assert result["shortfall_bps"] == pytest.approx(-100.0)

    def test_zero_arrival_price_returns_zero(self) -> None:
        result = compute_implementation_shortfall(
            arrival_price=0.0, fill_price=100.0, quantity=10.0,
        )
        assert result["shortfall_bps"] == 0.0
        assert result["shortfall_notional"] == 0.0

    def test_exact_fill_no_shortfall(self) -> None:
        result = compute_implementation_shortfall(
            arrival_price=50000.0, fill_price=50000.0, quantity=0.1, side="buy",
        )
        assert result["shortfall_bps"] == 0.0
        assert result["shortfall_notional"] == 0.0


# ---------------------------------------------------------------------------
# Arrival slippage
# ---------------------------------------------------------------------------


class TestArrivalSlippage:
    def test_buy_slippage_positive(self) -> None:
        slip = compute_arrival_slippage_bps(100.0, 100.20, "buy")
        assert slip == pytest.approx(20.0)

    def test_sell_slippage_positive(self) -> None:
        slip = compute_arrival_slippage_bps(100.0, 98.0, "sell")
        assert slip == pytest.approx(200.0)

    def test_zero_price(self) -> None:
        assert compute_arrival_slippage_bps(0.0, 100.0, "buy") == 0.0


# ---------------------------------------------------------------------------
# VWAP
# ---------------------------------------------------------------------------


class TestVWAP:
    def test_simple_vwap(self) -> None:
        vwap = compute_vwap([10.0, 12.0, 11.0], [100.0, 200.0, 300.0])
        # (10*100 + 12*200 + 11*300) / 600 = (1000+2400+3300)/600 = 6700/600
        assert vwap == pytest.approx(11.1667, abs=1e-4)

    def test_empty_lists(self) -> None:
        assert compute_vwap([], []) == 0.0

    def test_mismatched_lengths(self) -> None:
        assert compute_vwap([10.0, 12.0], [100.0]) == 0.0

    def test_zero_volume(self) -> None:
        assert compute_vwap([10.0, 12.0], [0.0, 0.0]) == 0.0

    def test_single_point(self) -> None:
        assert compute_vwap([42.0], [100.0]) == 42.0


# ---------------------------------------------------------------------------
# VWAP benchmark
# ---------------------------------------------------------------------------


class TestVWAPBenchmark:
    def test_buy_above_vwap_cost(self) -> None:
        result = compute_vwap_benchmark(
            fill_price=101.0, market_vwap=100.0, quantity=10.0, side="buy",
        )
        assert result["vwap_diff_bps"] == pytest.approx(100.0)
        assert result["vwap_cost_notional"] == pytest.approx(10.0)

    def test_sell_below_vwap_cost(self) -> None:
        result = compute_vwap_benchmark(
            fill_price=99.0, market_vwap=100.0, quantity=10.0, side="sell",
        )
        assert result["vwap_diff_bps"] == pytest.approx(100.0)

    def test_zero_vwap(self) -> None:
        result = compute_vwap_benchmark(100.0, 0.0, 10.0, "buy")
        assert result["vwap_diff_bps"] == 0.0


# ---------------------------------------------------------------------------
# Slippage report aggregation
# ---------------------------------------------------------------------------


class TestSlippageReport:
    def test_empty_records(self) -> None:
        report = aggregate_slippage_report([])
        assert report.total_fills == 0

    def test_single_record(self) -> None:
        records = [
            SlippageRecord(
                symbol="BTC/USDT", side="buy", quantity=0.1,
                arrival_price=50000.0, fill_price=50100.0,
            ),
        ]
        report = aggregate_slippage_report(records)
        assert report.total_fills == 1
        assert report.total_notional == pytest.approx(5010.0)
        assert report.avg_slippage_bps == pytest.approx(20.0)  # (100/50000)*10000

    def test_multiple_records_aggregated(self) -> None:
        records = [
            SlippageRecord(
                symbol="BTC/USDT", side="buy", quantity=0.1,
                arrival_price=50000.0, fill_price=50100.0,
            ),
            SlippageRecord(
                symbol="ETH/USDT", side="sell", quantity=5.0,
                arrival_price=3000.0, fill_price=2990.0,
            ),
        ]
        report = aggregate_slippage_report(records, symbol="ALL")
        assert report.total_fills == 2
        assert report.avg_slippage_bps > 0
        assert report.max_slippage_bps >= report.min_slippage_bps

    def test_with_vwap_benchmarks(self) -> None:
        records = [
            SlippageRecord(
                symbol="BTC/USDT", side="buy", quantity=0.1,
                arrival_price=50000.0, fill_price=50100.0,
                vwap_benchmark=50050.0,
            ),
        ]
        report = aggregate_slippage_report(records)
        assert report.avg_vwap_diff_bps == pytest.approx(9.99, abs=0.1)  # (50100-50050)/50050*10000

    def test_min_max_slippage(self) -> None:
        records = [
            SlippageRecord(symbol="A/USDT", side="buy", quantity=1.0,
                           arrival_price=100.0, fill_price=100.10),
            SlippageRecord(symbol="B/USDT", side="buy", quantity=1.0,
                           arrival_price=100.0, fill_price=100.50),
            SlippageRecord(symbol="C/USDT", side="buy", quantity=1.0,
                           arrival_price=100.0, fill_price=99.90),
        ]
        report = aggregate_slippage_report(records)
        assert report.max_slippage_bps == pytest.approx(50.0)
        assert report.min_slippage_bps == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


class TestGroupBySymbol:
    def test_groups_by_symbol(self) -> None:
        records = [
            SlippageRecord(symbol="BTC/USDT", side="buy", quantity=0.1,
                           arrival_price=50000.0, fill_price=50100.0),
            SlippageRecord(symbol="BTC/USDT", side="sell", quantity=0.05,
                           arrival_price=50100.0, fill_price=50050.0),
            SlippageRecord(symbol="ETH/USDT", side="buy", quantity=5.0,
                           arrival_price=3000.0, fill_price=3010.0),
        ]
        groups = group_slippage_by_symbol(records)
        assert "BTC/USDT" in groups
        assert "ETH/USDT" in groups
        assert groups["BTC/USDT"].total_fills == 2
        assert groups["ETH/USDT"].total_fills == 1


class TestGroupBySide:
    def test_groups_by_side(self) -> None:
        records = [
            SlippageRecord(symbol="BTC/USDT", side="buy", quantity=0.1,
                           arrival_price=50000.0, fill_price=50100.0),
            SlippageRecord(symbol="ETH/USDT", side="buy", quantity=5.0,
                           arrival_price=3000.0, fill_price=3010.0),
            SlippageRecord(symbol="BTC/USDT", side="sell", quantity=0.05,
                           arrival_price=51000.0, fill_price=50900.0),
        ]
        groups = group_slippage_by_side(records)
        assert groups["buy"].total_fills == 2
        assert groups["sell"].total_fills == 1

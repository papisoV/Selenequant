"""Tests for Paper-to-Live fill deviation analysis module."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

# Direct imports bypassing selene_space_alpha.__init__ (which triggers torch)
_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from attribution.sim_live_deviation import (
    DeviationReport,
    FillRecord,
    aggregate_deviations,
    compare_fills,
    detect_anomalies,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paper(
    symbol: str = "BTC/USDT",
    side: str = "buy",
    price: float = 50_000.0,
    quantity: float = 1.0,
    fee: float = 10.0,
    ts: int = 1_700_000_000_000,
    latency_ms: int | None = 50,
) -> FillRecord:
    """Shorthand to create a paper FillRecord."""
    return FillRecord(
        source="paper",
        symbol=symbol,
        side=side,
        price=price,
        quantity=quantity,
        fee=fee,
        timestamp_ms=ts,
        latency_ms=latency_ms,
    )


def _live(
    symbol: str = "BTC/USDT",
    side: str = "buy",
    price: float = 50_000.0,
    quantity: float = 1.0,
    fee: float = 10.0,
    ts: int = 1_700_000_000_000,
    latency_ms: int | None = 80,
) -> FillRecord:
    """Shorthand to create a live FillRecord."""
    return FillRecord(
        source="live",
        symbol=symbol,
        side=side,
        price=price,
        quantity=quantity,
        fee=fee,
        timestamp_ms=ts,
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# compare_fills
# ---------------------------------------------------------------------------


class TestCompareFills:
    """Tests for fill matching and deviation computation."""

    def test_matched_fills_correct_deviations(self) -> None:
        """Matched paper/live fills produce correct deviation values."""
        paper = _paper(price=50_010.0, fee=12.0, latency_ms=50, ts=1_000)
        live = _live(price=50_000.0, fee=10.0, latency_ms=80, ts=1_050)

        reports = compare_fills([paper], [live])

        assert len(reports) == 1
        r = reports[0]
        assert r.symbol == "BTC/USDT"
        # price_deviation_bps = (50010 - 50000) / 50000 * 10000 = 2.0
        assert r.price_deviation_bps == pytest.approx(2.0, abs=1e-6)
        # fee_deviation_pct = (12 - 10) / 10 * 100 = 20.0
        assert r.fee_deviation_pct == pytest.approx(20.0, abs=1e-6)
        # latency_deviation_ms = 50 - 80 = -30
        assert r.latency_deviation_ms == pytest.approx(-30.0, abs=1e-6)
        assert r.quantity_match is True
        assert r.n_comparisons == 1

    def test_quantity_mismatch_detected(self) -> None:
        """Different quantities set quantity_match to False."""
        paper = _paper(quantity=1.0)
        live = _live(quantity=0.5)

        reports = compare_fills([paper], [live])

        assert len(reports) == 1
        assert reports[0].quantity_match is False

    def test_unmatched_fills_skipped(self) -> None:
        """Fills with no matching symbol+side are skipped."""
        paper = _paper(symbol="BTC/USDT", side="buy")
        live = _live(symbol="ETH/USDT", side="buy")  # different symbol

        reports = compare_fills([paper], [live])
        assert len(reports) == 0

    def test_unmatched_side_skipped(self) -> None:
        """Fills with different side are not matched."""
        paper = _paper(side="buy")
        live = _live(side="sell")

        reports = compare_fills([paper], [live])
        assert len(reports) == 0

    def test_timestamp_too_far_skipped(self) -> None:
        """Fills beyond max_time_diff_ms are not matched."""
        paper = _paper(ts=1_000)
        live = _live(ts=70_000)  # 69 seconds apart, default max is 60s

        reports = compare_fills([paper], [live], max_time_diff_ms=60_000)
        assert len(reports) == 0

    def test_timestamp_within_custom_window_matched(self) -> None:
        """Custom max_time_diff_ms allows wider matching."""
        paper = _paper(ts=1_000)
        live = _live(ts=70_000)

        reports = compare_fills([paper], [live], max_time_diff_ms=120_000)
        assert len(reports) == 1

    def test_multiple_symbols_grouped(self) -> None:
        """Multiple symbols produce separate reports."""
        paper = [
            _paper(symbol="BTC/USDT", price=50_010.0, ts=1_000),
            _paper(symbol="ETH/USDT", price=3_010.0, ts=1_000),
        ]
        live = [
            _live(symbol="BTC/USDT", price=50_000.0, ts=1_050),
            _live(symbol="ETH/USDT", price=3_000.0, ts=1_050),
        ]

        reports = compare_fills(paper, live)
        assert len(reports) == 2
        symbols = {r.symbol for r in reports}
        assert symbols == {"BTC/USDT", "ETH/USDT"}

    def test_multiple_fills_same_symbol_averaged(self) -> None:
        """Multiple matched pairs for same symbol are averaged."""
        paper = [
            _paper(price=50_010.0, fee=12.0, ts=1_000),
            _paper(price=50_020.0, fee=14.0, ts=2_000),
        ]
        live = [
            _live(price=50_000.0, fee=10.0, ts=1_050),
            _live(price=50_000.0, fee=10.0, ts=2_050),
        ]

        reports = compare_fills(paper, live)
        assert len(reports) == 1
        r = reports[0]
        # price bps: 2.0 and 4.0 -> mean 3.0
        assert r.price_deviation_bps == pytest.approx(3.0, abs=1e-6)
        # fee pct: 20.0 and 40.0 -> mean 30.0
        assert r.fee_deviation_pct == pytest.approx(30.0, abs=1e-6)
        assert r.n_comparisons == 2

    def test_greedy_matching_no_reuse(self) -> None:
        """Each live fill is matched at most once."""
        paper = [
            _paper(ts=1_000),
            _paper(ts=1_100),
        ]
        live = [
            _live(ts=1_050),  # only one live fill
        ]

        reports = compare_fills(paper, live)
        assert len(reports) == 1
        assert reports[0].n_comparisons == 1

    def test_no_latency_gives_nan(self) -> None:
        """Missing latency data results in NaN latency_deviation_ms."""
        paper = _paper(latency_ms=None)
        live = _live(latency_ms=80)

        reports = compare_fills([paper], [live])
        assert len(reports) == 1
        assert math.isnan(reports[0].latency_deviation_ms)

    def test_negative_price_deviation(self) -> None:
        """Paper price below live price gives negative bps."""
        paper = _paper(price=49_990.0)
        live = _live(price=50_000.0)

        reports = compare_fills([paper], [live])
        assert reports[0].price_deviation_bps == pytest.approx(-2.0, abs=1e-6)


# ---------------------------------------------------------------------------
# aggregate_deviations
# ---------------------------------------------------------------------------


class TestAggregateDeviations:
    """Tests for cross-symbol aggregation."""

    def test_correct_mean_median_std(self) -> None:
        """Aggregation computes correct mean, median, std."""
        reports = [
            DeviationReport(
                symbol="A",
                price_deviation_bps=2.0,
                fee_deviation_pct=10.0,
                latency_deviation_ms=-30.0,
                quantity_match=True,
                n_comparisons=1,
            ),
            DeviationReport(
                symbol="B",
                price_deviation_bps=4.0,
                fee_deviation_pct=20.0,
                latency_deviation_ms=-10.0,
                quantity_match=False,
                n_comparisons=1,
            ),
            DeviationReport(
                symbol="C",
                price_deviation_bps=6.0,
                fee_deviation_pct=30.0,
                latency_deviation_ms=-50.0,
                quantity_match=True,
                n_comparisons=1,
            ),
        ]

        agg = aggregate_deviations(reports)

        # price: [2, 4, 6] -> mean=4, median=4, std=std([2,4,6], ddof=0)=sqrt(8/3)
        assert agg["price_deviation_bps_mean"] == pytest.approx(4.0, abs=1e-6)
        assert agg["price_deviation_bps_median"] == pytest.approx(4.0, abs=1e-6)
        expected_std = float(np.std([2.0, 4.0, 6.0], ddof=0))
        assert agg["price_deviation_bps_std"] == pytest.approx(expected_std, abs=1e-6)

        # fee: [10, 20, 30] -> mean=20, median=20
        assert agg["fee_deviation_pct_mean"] == pytest.approx(20.0, abs=1e-6)
        assert agg["fee_deviation_pct_median"] == pytest.approx(20.0, abs=1e-6)

        # latency: [-30, -10, -50] -> mean=-30, median=-30
        assert agg["latency_deviation_ms_mean"] == pytest.approx(-30.0, abs=1e-6)
        assert agg["latency_deviation_ms_median"] == pytest.approx(-30.0, abs=1e-6)

        # fill_match_rate: 2/3
        assert agg["fill_match_rate"] == pytest.approx(2.0 / 3.0, abs=1e-6)

    def test_empty_reports_returns_empty_dict(self) -> None:
        """Empty input returns an empty dict."""
        assert aggregate_deviations([]) == {}

    def test_single_report(self) -> None:
        """Single report: mean == median == value, std == 0."""
        reports = [
            DeviationReport(
                symbol="A",
                price_deviation_bps=5.0,
                fee_deviation_pct=15.0,
                latency_deviation_ms=-20.0,
                quantity_match=True,
                n_comparisons=1,
            ),
        ]

        agg = aggregate_deviations(reports)
        assert agg["price_deviation_bps_mean"] == pytest.approx(5.0, abs=1e-6)
        assert agg["price_deviation_bps_median"] == pytest.approx(5.0, abs=1e-6)
        assert agg["price_deviation_bps_std"] == pytest.approx(0.0, abs=1e-6)
        assert agg["fill_match_rate"] == pytest.approx(1.0, abs=1e-6)

    def test_nan_latency_excluded_from_stats(self) -> None:
        """NaN latency values are excluded from mean/median/std."""
        reports = [
            DeviationReport(
                symbol="A",
                price_deviation_bps=1.0,
                fee_deviation_pct=5.0,
                latency_deviation_ms=float("nan"),
                quantity_match=True,
                n_comparisons=1,
            ),
            DeviationReport(
                symbol="B",
                price_deviation_bps=2.0,
                fee_deviation_pct=10.0,
                latency_deviation_ms=-20.0,
                quantity_match=True,
                n_comparisons=1,
            ),
        ]

        agg = aggregate_deviations(reports)
        # Only one non-NaN latency value: -20.0
        assert agg["latency_deviation_ms_mean"] == pytest.approx(-20.0, abs=1e-6)
        assert agg["latency_deviation_ms_median"] == pytest.approx(-20.0, abs=1e-6)
        assert agg["latency_deviation_ms_std"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# detect_anomalies
# ---------------------------------------------------------------------------


class TestDetectAnomalies:
    """Tests for anomaly detection."""

    def test_flags_high_price_deviation(self) -> None:
        """Reports with price deviation above threshold are flagged."""
        reports = [
            DeviationReport(
                symbol="A",
                price_deviation_bps=15.0,  # > 10 bps default
                fee_deviation_pct=5.0,
                latency_deviation_ms=0.0,
                quantity_match=True,
                n_comparisons=1,
            ),
        ]

        anomalies = detect_anomalies(reports)
        assert len(anomalies) == 1
        assert anomalies[0].symbol == "A"

    def test_flags_high_fee_deviation(self) -> None:
        """Reports with fee deviation above threshold are flagged."""
        reports = [
            DeviationReport(
                symbol="B",
                price_deviation_bps=1.0,
                fee_deviation_pct=60.0,  # > 50% default
                latency_deviation_ms=0.0,
                quantity_match=True,
                n_comparisons=1,
            ),
        ]

        anomalies = detect_anomalies(reports)
        assert len(anomalies) == 1

    def test_passes_low_deviations(self) -> None:
        """Reports within thresholds are not flagged."""
        reports = [
            DeviationReport(
                symbol="C",
                price_deviation_bps=5.0,  # < 10 bps
                fee_deviation_pct=20.0,  # < 50%
                latency_deviation_ms=0.0,
                quantity_match=True,
                n_comparisons=1,
            ),
        ]

        anomalies = detect_anomalies(reports)
        assert len(anomalies) == 0

    def test_custom_thresholds(self) -> None:
        """Custom thresholds are respected."""
        reports = [
            DeviationReport(
                symbol="D",
                price_deviation_bps=5.0,
                fee_deviation_pct=20.0,
                latency_deviation_ms=0.0,
                quantity_match=True,
                n_comparisons=1,
            ),
        ]

        # Tight thresholds should flag this
        anomalies = detect_anomalies(reports, price_threshold_bps=3.0, fee_threshold_pct=10.0)
        assert len(anomalies) == 1

    def test_nan_not_flagged(self) -> None:
        """NaN deviations are not flagged as anomalous."""
        reports = [
            DeviationReport(
                symbol="E",
                price_deviation_bps=float("nan"),
                fee_deviation_pct=float("nan"),
                latency_deviation_ms=0.0,
                quantity_match=True,
                n_comparisons=1,
            ),
        ]

        anomalies = detect_anomalies(reports)
        assert len(anomalies) == 0

    def test_mixed_reports(self) -> None:
        """Only anomalous reports are returned from a mixed list."""
        reports = [
            DeviationReport("A", 15.0, 5.0, 0.0, True, 1),   # price anomalous
            DeviationReport("B", 1.0, 20.0, 0.0, True, 1),    # OK
            DeviationReport("C", 1.0, 60.0, 0.0, True, 1),    # fee anomalous
        ]

        anomalies = detect_anomalies(reports)
        assert len(anomalies) == 2
        assert {a.symbol for a in anomalies} == {"A", "C"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases: empty inputs, single fill, identical fills."""

    def test_empty_paper_fills(self) -> None:
        """Empty paper fills produce no reports."""
        reports = compare_fills([], [_live()])
        assert len(reports) == 0

    def test_empty_live_fills(self) -> None:
        """Empty live fills produce no reports."""
        reports = compare_fills([_paper()], [])
        assert len(reports) == 0

    def test_both_empty(self) -> None:
        """Both empty produces no reports."""
        reports = compare_fills([], [])
        assert len(reports) == 0

    def test_single_fill_pair(self) -> None:
        """Single matched pair produces one report."""
        paper = _paper(price=50_010.0, ts=1_000)
        live = _live(price=50_000.0, ts=1_050)

        reports = compare_fills([paper], [live])
        assert len(reports) == 1
        assert reports[0].n_comparisons == 1

    def test_identical_fills_zero_deviation(self) -> None:
        """Identical paper and live fills produce zero deviations."""
        ts = 1_700_000_000_000
        paper = _paper(price=50_000.0, fee=10.0, quantity=1.0, latency_ms=50, ts=ts)
        live = _live(price=50_000.0, fee=10.0, quantity=1.0, latency_ms=50, ts=ts)

        reports = compare_fills([paper], [live])
        assert len(reports) == 1
        r = reports[0]
        assert r.price_deviation_bps == pytest.approx(0.0, abs=1e-6)
        assert r.fee_deviation_pct == pytest.approx(0.0, abs=1e-6)
        assert r.latency_deviation_ms == pytest.approx(0.0, abs=1e-6)
        assert r.quantity_match is True

    def test_identical_fills_not_anomalous(self) -> None:
        """Zero-deviation fills are not flagged as anomalies."""
        reports = [
            DeviationReport("A", 0.0, 0.0, 0.0, True, 1),
        ]
        anomalies = detect_anomalies(reports)
        assert len(anomalies) == 0

    def test_aggregate_identical_fills(self) -> None:
        """Aggregation of zero-deviation reports gives zero stats."""
        reports = [
            DeviationReport("A", 0.0, 0.0, 0.0, True, 1),
            DeviationReport("B", 0.0, 0.0, 0.0, True, 1),
        ]
        agg = aggregate_deviations(reports)
        assert agg["price_deviation_bps_mean"] == pytest.approx(0.0, abs=1e-6)
        assert agg["fee_deviation_pct_mean"] == pytest.approx(0.0, abs=1e-6)
        assert agg["latency_deviation_ms_mean"] == pytest.approx(0.0, abs=1e-6)
        assert agg["fill_match_rate"] == pytest.approx(1.0, abs=1e-6)

    def test_detect_anomalies_empty_input(self) -> None:
        """Empty input to detect_anomalies returns empty list."""
        assert detect_anomalies([]) == []

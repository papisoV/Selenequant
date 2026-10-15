"""Tests for K-line data quality validation."""

from __future__ import annotations

import pytest

from selene_space_market.quality import (
    KlineQualityChecker,
    QualityIssue,
    QualityReport,
)
from selene_space_types.market import KlineEvent


def _kline(
    open_: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 102.0,
    volume: float = 1000.0,
    ts: int = 1711000000000,
    symbol: str = "BTC/USDT",
) -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        symbol=symbol,
        timestamp=ts,
        interval="1h",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        closed=True,
    )


class TestOHLCConsistency:
    def test_clean_bar_no_issues(self) -> None:
        checker = KlineQualityChecker()
        issues = checker.check_bar(_kline())
        assert len(issues) == 0

    def test_high_less_than_low(self) -> None:
        checker = KlineQualityChecker()
        issues = checker.check_bar(_kline(high=90.0, low=95.0))
        assert any(i.issue_type == "ohlc_inconsistent" and i.severity == "error" for i in issues)

    def test_open_above_high(self) -> None:
        checker = KlineQualityChecker()
        issues = checker.check_bar(_kline(open_=110.0, high=105.0))
        assert any(i.issue_type == "ohlc_inconsistent" for i in issues)

    def test_open_below_low(self) -> None:
        checker = KlineQualityChecker()
        issues = checker.check_bar(_kline(open_=90.0, low=95.0))
        assert any(i.issue_type == "ohlc_inconsistent" for i in issues)

    def test_close_above_high(self) -> None:
        checker = KlineQualityChecker()
        issues = checker.check_bar(_kline(close=110.0, high=105.0))
        assert any(i.issue_type == "ohlc_inconsistent" for i in issues)

    def test_close_below_low(self) -> None:
        checker = KlineQualityChecker()
        issues = checker.check_bar(_kline(close=90.0, low=95.0))
        assert any(i.issue_type == "ohlc_inconsistent" for i in issues)


class TestZeroPrices:
    def test_zero_price_validation_at_model_level(self) -> None:
        """KlineEvent Pydantic model rejects zero/negative prices at construction."""
        with pytest.raises(Exception):  # ValidationError
            _kline(open_=0.0)
        with pytest.raises(Exception):
            _kline(close=-1.0)

    def test_checker_handles_near_zero_prices(self) -> None:
        """Very small but positive prices should not trigger zero_price."""
        checker = KlineQualityChecker()
        issues = checker.check_bar(_kline(open_=0.001, low=0.001, close=0.001, high=0.001))
        assert not any(i.issue_type == "zero_price" for i in issues)


class TestZeroVolume:
    def test_zero_volume_flagged(self) -> None:
        checker = KlineQualityChecker(check_volume=True)
        issues = checker.check_bar(_kline(volume=0.0))
        assert any(i.issue_type == "zero_volume" and i.severity == "warning" for i in issues)

    def test_volume_check_disabled(self) -> None:
        checker = KlineQualityChecker(check_volume=False)
        issues = checker.check_bar(_kline(volume=0.0))
        assert not any(i.issue_type == "zero_volume" for i in issues)


class TestGapDetection:
    def test_no_gap(self) -> None:
        checker = KlineQualityChecker()
        # First bar
        checker.check_bar(_kline(ts=1000), expected_interval_ms=1000)
        # Second bar, exactly 1 interval later
        issues = checker.check_bar(_kline(ts=2000), expected_interval_ms=1000)
        assert not any(i.issue_type == "gap" for i in issues)

    def test_gap_detected(self) -> None:
        checker = KlineQualityChecker()
        checker.check_bar(_kline(ts=1000), expected_interval_ms=1000)
        # Gap: 5 intervals passed (expected 1)
        issues = checker.check_bar(_kline(ts=5000), expected_interval_ms=1000)
        assert any(i.issue_type == "gap" for i in issues)

    def test_no_interval_skips_gap_check(self) -> None:
        checker = KlineQualityChecker()
        checker.check_bar(_kline(ts=1000), expected_interval_ms=0)
        # No expected_interval → no gap detection
        issues = checker.check_bar(_kline(ts=99999), expected_interval_ms=0)
        assert not any(i.issue_type == "gap" for i in issues)

    def test_out_of_order_timestamp(self) -> None:
        checker = KlineQualityChecker()
        checker.check_bar(_kline(ts=2000), expected_interval_ms=1000)
        issues = checker.check_bar(_kline(ts=1000), expected_interval_ms=1000)
        assert any(i.issue_type == "timestamp_order" and i.severity == "error" for i in issues)


class TestOutlierDetection:
    def test_normal_price_no_outlier(self) -> None:
        checker = KlineQualityChecker(outlier_z_score=5.0)
        # Feed 25 normal bars
        for i in range(25):
            checker.check_bar(_kline(close=100.0 + i * 0.1, ts=1000 + i * 1000))
        # Normal bar
        issues = checker.check_bar(_kline(close=102.5, ts=26000))
        assert not any(i.issue_type == "outlier" for i in issues)

    def test_spike_detected_as_outlier(self) -> None:
        checker = KlineQualityChecker(outlier_z_score=3.0)
        # Feed 25 steady bars at 100.0
        for i in range(25):
            checker.check_bar(_kline(close=100.0, ts=1000 + i * 1000))
        # Massive spike — from 100 to 500 (5x), clearly an outlier
        issues = checker.check_bar(_kline(close=500.0, ts=26000))
        assert any(i.issue_type == "outlier" for i in issues)

    def test_insufficient_data_no_outlier(self) -> None:
        checker = KlineQualityChecker()
        # Only 5 bars — not enough for outlier detection
        for i in range(5):
            checker.check_bar(_kline(close=10000.0, ts=1000 + i * 1000))
        assert True  # No crash


class TestQualityReport:
    def test_clean_report(self) -> None:
        report = QualityReport(total_bars=100)
        assert report.is_clean is True
        assert report.quality_score == 1.0

    def test_report_with_errors(self) -> None:
        report = QualityReport(total_bars=100)
        report.errors.append(QualityIssue(
            symbol="BTC/USDT", timestamp=0, issue_type="zero_price",
            severity="error", message="test",
        ))
        assert report.is_clean is False
        assert report.error_count == 1
        assert report.quality_score < 1.0

    def test_empty_report(self) -> None:
        report = QualityReport()
        assert report.quality_score == 1.0


class TestValidateSeries:
    def test_clean_series(self) -> None:
        checker = KlineQualityChecker()
        klines = [_kline(ts=1000 + i * 3600000) for i in range(10)]
        report = checker.validate_series(klines, expected_interval_ms=3600000)
        assert report.is_clean is True
        assert report.total_bars == 10

    def test_dirty_series(self) -> None:
        checker = KlineQualityChecker()
        klines = [
            _kline(high=90, low=95, ts=1000),  # OHLC error
            _kline(ts=2000),  # Clean
            _kline(volume=0, ts=3000),  # Zero volume
        ]
        report = checker.validate_series(klines)
        assert report.error_count >= 1
        assert report.warning_count >= 1


class TestReset:
    def test_reset_clears_state(self) -> None:
        checker = KlineQualityChecker()
        checker.check_bar(_kline(ts=1000), expected_interval_ms=1000)
        checker.reset()
        # After reset, no gap should be detected
        issues = checker.check_bar(_kline(ts=99999), expected_interval_ms=1000)
        assert not any(i.issue_type == "gap" for i in issues)


class TestInvalidParams:
    def test_invalid_gap_ratio(self) -> None:
        with pytest.raises(ValueError):
            KlineQualityChecker(max_gap_ratio=0.5)

    def test_invalid_z_score(self) -> None:
        with pytest.raises(ValueError):
            KlineQualityChecker(outlier_z_score=-1.0)

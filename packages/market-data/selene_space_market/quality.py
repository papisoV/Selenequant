"""Data quality validation for K-line (OHLCV) data.

Detects common data issues that silently corrupt backtests:
  - OHLC inconsistency: high < low, open/close outside [low, high]
  - Zero/negative prices
  - Zero volume (may indicate missing data)
  - Price gaps (missing bars between consecutive timestamps)
  - Outlier prices (sudden spikes/drops beyond threshold)
  - Timestamp ordering issues
"""

from __future__ import annotations

from dataclasses import dataclass, field

from selene_space_types.market import KlineEvent


@dataclass(frozen=True)
class QualityIssue:
    """A single data quality issue detected in a kline bar."""

    symbol: str
    timestamp: int
    issue_type: str  # "ohlc_inconsistent", "zero_price", "zero_volume", "gap", "outlier", "timestamp_order"
    severity: str    # "error", "warning"
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class QualityReport:
    """Summary of data quality validation results."""

    total_bars: int = 0
    errors: list[QualityIssue] = field(default_factory=list)
    warnings: list[QualityIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def is_clean(self) -> bool:
        return self.error_count == 0

    @property
    def quality_score(self) -> float:
        """0.0 = all bars have errors, 1.0 = no issues at all."""
        if self.total_bars == 0:
            return 1.0
        issues_per_bar = (self.error_count + self.warning_count) / self.total_bars
        return max(0.0, 1.0 - issues_per_bar)


class KlineQualityChecker:
    """Validates K-line data quality.

    Args:
        max_gap_ratio: Max allowed gap between bars as ratio of expected interval.
            E.g. 2.0 means a gap of 2× the expected interval is flagged.
        outlier_z_score: Price change beyond this many standard deviations
            from the rolling mean is flagged as outlier. Default 5.0.
        check_volume: Whether to flag zero-volume bars. Default True.
    """

    def __init__(
        self,
        max_gap_ratio: float = 2.0,
        outlier_z_score: float = 5.0,
        check_volume: bool = True,
    ) -> None:
        if max_gap_ratio < 1.0:
            raise ValueError("max_gap_ratio must be >= 1.0")
        if outlier_z_score <= 0:
            raise ValueError("outlier_z_score must be positive")
        self._max_gap_ratio = max_gap_ratio
        self._outlier_z = outlier_z_score
        self._check_volume = check_volume
        self._prev_timestamps: dict[str, int] = {}
        self._price_stats: dict[str, _RollingStats] = {}

    def check_bar(self, kline: KlineEvent, expected_interval_ms: int = 0) -> list[QualityIssue]:
        """Validate a single kline bar.

        Args:
            kline: The kline bar to validate.
            expected_interval_ms: Expected interval between bars in ms
                (e.g. 3600000 for 1h). 0 = skip gap detection.

        Returns:
            List of QualityIssue objects (empty if bar is clean).
        """
        issues: list[QualityIssue] = []
        sym = kline.symbol
        ts = kline.timestamp

        # 1. Zero/negative price checks
        for name, val in [("open", kline.open), ("high", kline.high),
                          ("low", kline.low), ("close", kline.close)]:
            if val <= 0:
                issues.append(QualityIssue(
                    symbol=sym, timestamp=ts, issue_type="zero_price",
                    severity="error", message=f"{name} price is {val} (must be > 0)",
                    details={"field": name, "value": val},
                ))

        # 2. OHLC consistency
        if kline.high < kline.low:
            issues.append(QualityIssue(
                symbol=sym, timestamp=ts, issue_type="ohlc_inconsistent",
                severity="error",
                message=f"high ({kline.high:.2f}) < low ({kline.low:.2f})",
                details={"high": kline.high, "low": kline.low},
            ))

        if kline.open > kline.high or kline.open < kline.low:
            issues.append(QualityIssue(
                symbol=sym, timestamp=ts, issue_type="ohlc_inconsistent",
                severity="error",
                message=f"open ({kline.open:.2f}) outside [low, high] range",
                details={"open": kline.open, "high": kline.high, "low": kline.low},
            ))

        if kline.close > kline.high or kline.close < kline.low:
            issues.append(QualityIssue(
                symbol=sym, timestamp=ts, issue_type="ohlc_inconsistent",
                severity="error",
                message=f"close ({kline.close:.2f}) outside [low, high] range",
                details={"close": kline.close, "high": kline.high, "low": kline.low},
            ))

        # 3. Zero volume
        if self._check_volume and kline.volume == 0:
            issues.append(QualityIssue(
                symbol=sym, timestamp=ts, issue_type="zero_volume",
                severity="warning", message="Volume is zero (may indicate missing data)",
            ))

        # 4. Gap detection
        if expected_interval_ms > 0:
            prev_ts = self._prev_timestamps.get(sym)
            if prev_ts is not None:
                gap = ts - prev_ts
                if gap > expected_interval_ms * self._max_gap_ratio:
                    missing_bars = int(gap / expected_interval_ms) - 1
                    issues.append(QualityIssue(
                        symbol=sym, timestamp=ts, issue_type="gap",
                        severity="warning",
                        message=f"Gap of {gap}ms (expected {expected_interval_ms}ms), ~{missing_bars} missing bars",
                        details={"gap_ms": gap, "expected_ms": expected_interval_ms,
                                 "missing_bars": missing_bars},
                    ))
                elif gap <= 0:
                    issues.append(QualityIssue(
                        symbol=sym, timestamp=ts, issue_type="timestamp_order",
                        severity="error",
                        message=f"Timestamp {ts} <= previous {prev_ts} (out of order or duplicate)",
                        details={"timestamp": ts, "previous": prev_ts},
                    ))
            self._prev_timestamps[sym] = ts

        # 5. Outlier detection (rolling z-score on close price)
        stats = self._price_stats.setdefault(sym, _RollingStats())
        if stats.count >= 20:
            if stats.std > 0:
                z = abs((kline.close - stats.mean) / stats.std)
            elif abs(kline.close - stats.mean) > 0:
                # std=0 but new value differs from mean → definite outlier
                z = float('inf')
            else:
                z = 0.0
            if z > self._outlier_z:
                issues.append(QualityIssue(
                    symbol=sym, timestamp=ts, issue_type="outlier",
                    severity="warning",
                    message=f"Close price {kline.close:.2f} is {z:.1f}σ from rolling mean ({stats.mean:.2f})",
                    details={"price": kline.close, "z_score": z, "rolling_mean": stats.mean},
                ))
        stats.update(kline.close)

        return issues

    def validate_series(self, klines: list[KlineEvent], expected_interval_ms: int = 0) -> QualityReport:
        """Validate a series of kline bars.

        Args:
            klines: List of kline bars to validate (must be in timestamp order).
            expected_interval_ms: Expected interval between bars in ms.

        Returns:
            QualityReport with all issues found.
        """
        report = QualityReport(total_bars=len(klines))
        for kline in klines:
            issues = self.check_bar(kline, expected_interval_ms)
            for issue in issues:
                if issue.severity == "error":
                    report.errors.append(issue)
                else:
                    report.warnings.append(issue)
        return report

    def reset(self) -> None:
        """Reset internal state (previous timestamps, rolling stats)."""
        self._prev_timestamps.clear()
        self._price_stats.clear()


class _RollingStats:
    """Welford's online algorithm for rolling mean and standard deviation."""

    def __init__(self) -> None:
        self.count: int = 0
        self.mean: float = 0.0
        self._m2: float = 0.0

    @property
    def std(self) -> float:
        if self.count < 2:
            return 0.0
        return (self._m2 / (self.count - 1)) ** 0.5

    def update(self, value: float) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        delta2 = value - self.mean
        self._m2 += delta * delta2

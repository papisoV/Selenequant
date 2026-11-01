"""Behavior locks for selene_space_security.guardrails.hallucination:
price-deviation grading, indicator existence, confidence plausibility."""

from selene_space_security.guardrails.hallucination import HallucinationDetector
from selene_space_types import RiskLevel

# --- price deviation -----------------------------------------------------------


def test_one_percent_drift_stays_silent() -> None:
    detector = HallucinationDetector()
    assert detector.check_price_reference("BTC/USDT", 50_500, 50_000) is None


def test_ten_percent_drift_raises_restricted() -> None:
    detector = HallucinationDetector()
    finding = detector.check_price_reference("BTC/USDT", 55_500, 50_000)
    assert finding is not None
    assert finding.severity == RiskLevel.RESTRICTED
    assert finding.deviation_pct >= 0.10


def test_twenty_five_percent_drift_raises_dangerous() -> None:
    detector = HallucinationDetector()
    finding = detector.check_price_reference("BTC/USDT", 62_500, 50_000)
    assert finding is not None
    assert finding.severity == RiskLevel.DANGEROUS


def test_fifty_percent_drift_blocks() -> None:
    detector = HallucinationDetector()
    finding = detector.check_price_reference("BTC/USDT", 75_000, 50_000)
    assert finding is not None
    assert finding.severity == RiskLevel.BLOCKED


def test_zero_reference_treated_as_dangerous() -> None:
    detector = HallucinationDetector()
    finding = detector.check_price_reference("BTC/USDT", 100, 0)
    assert finding is not None
    assert finding.severity == RiskLevel.DANGEROUS


def test_negative_reference_produces_a_finding() -> None:
    detector = HallucinationDetector()
    assert detector.check_price_reference("BTC/USDT", 100, -10) is not None


def test_exact_match_is_silent() -> None:
    detector = HallucinationDetector()
    assert detector.check_price_reference("BTC/USDT", 50_000, 50_000) is None


def test_lowered_threshold_catches_smaller_drift() -> None:
    strict = HallucinationDetector(price_deviation_threshold=0.05)
    # 8% drift trips a 5% threshold.
    assert strict.check_price_reference("BTC/USDT", 54_000, 50_000) is not None


def test_nine_point_nine_percent_stays_under_default_threshold() -> None:
    detector = HallucinationDetector()
    assert detector.check_price_reference("BTC/USDT", 54_950, 50_000) is None


def test_finding_message_names_symbol_and_claimed_price() -> None:
    detector = HallucinationDetector()
    finding = detector.check_price_reference("ETH/USDT", 4_000, 3_000)
    assert finding is not None
    assert "ETH/USDT" in finding.message
    assert "4000" in finding.message


# --- indicator existence -----------------------------------------------------------


KNOWN = ["sma", "ema", "rsi", "macd", "atr", "adx"]


def test_all_standard_indicators_recognized() -> None:
    detector = HallucinationDetector()
    assert all(detector.check_indicator_exists(n) for n in KNOWN)


def test_invented_indicator_rejected() -> None:
    detector = HallucinationDetector()
    assert detector.check_indicator_exists("quantum_momentum_flux") is False


def test_lookup_case_insensitive() -> None:
    detector = HallucinationDetector()
    assert detector.check_indicator_exists("RSI") is True
    assert detector.check_indicator_exists("MACD") is True


def test_caller_supplied_universe_overrides_default() -> None:
    detector = HallucinationDetector()
    universe = frozenset({"custom_indicator"})
    assert detector.check_indicator_exists("custom_indicator", known=universe) is True
    assert detector.check_indicator_exists("sma", known=universe) is False


# --- confidence plausibility ----------------------------------------------------------


def test_typical_confidence_passes() -> None:
    assert HallucinationDetector().check_confidence_reasonable(0.75) is True


def test_zero_confidence_passes() -> None:
    assert HallucinationDetector().check_confidence_reasonable(0.0) is True


def test_perfect_confidence_flagged() -> None:
    assert HallucinationDetector().check_confidence_reasonable(1.0) is False


def test_custom_ceiling_enforced() -> None:
    detector = HallucinationDetector()
    assert detector.check_confidence_reasonable(0.95, max_confidence=0.90) is False

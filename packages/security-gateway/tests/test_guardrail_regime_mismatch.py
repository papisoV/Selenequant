"""Behavior locks for selene_space_security.guardrails.regime_mismatch:
compatibility matrix, mismatch grading, volatility gate, custom overrides."""

from selene_space_security.guardrails.regime_mismatch import (
    STRATEGY_REGIME_COMPAT,
    RegimeMismatchDetector,
)
from selene_space_types import MarketRegime, RiskLevel, StrategyType

# --- compatibility matrix -------------------------------------------------------


def test_sma_cross_wants_trend_not_range() -> None:
    sma = STRATEGY_REGIME_COMPAT[StrategyType.SMA_CROSS]
    assert MarketRegime.TRENDING in sma
    assert MarketRegime.RANGING not in sma


def test_rsi_reversal_wants_range_not_trend() -> None:
    rsi = STRATEGY_REGIME_COMPAT[StrategyType.RSI_REVERSAL]
    assert MarketRegime.RANGING in rsi
    assert MarketRegime.TRENDING not in rsi


def test_macd_tolerates_trend_and_volatility() -> None:
    macd = STRATEGY_REGIME_COMPAT[StrategyType.MACD]
    assert {MarketRegime.TRENDING, MarketRegime.VOLATILE} <= macd


def test_custom_strategy_admits_all_regimes() -> None:
    assert len(STRATEGY_REGIME_COMPAT[StrategyType.CUSTOM]) == 3


# --- check_mismatch -----------------------------------------------------------------


def test_sma_in_trending_is_clean() -> None:
    detector = RegimeMismatchDetector()
    assert detector.check_mismatch(StrategyType.SMA_CROSS, MarketRegime.TRENDING) is None


def test_sma_in_ranging_flags_with_expected_set() -> None:
    detector = RegimeMismatchDetector()
    finding = detector.check_mismatch(StrategyType.SMA_CROSS, MarketRegime.RANGING)
    assert finding is not None
    assert finding.strategy_type == StrategyType.SMA_CROSS
    assert finding.current_regime == MarketRegime.RANGING
    assert MarketRegime.TRENDING in finding.expected_regimes


def test_rsi_in_trending_flags() -> None:
    detector = RegimeMismatchDetector()
    assert detector.check_mismatch(StrategyType.RSI_REVERSAL, MarketRegime.TRENDING) is not None


def test_rsi_in_ranging_is_clean() -> None:
    detector = RegimeMismatchDetector()
    assert detector.check_mismatch(StrategyType.RSI_REVERSAL, MarketRegime.RANGING) is None


def test_custom_strategy_never_flags_any_regime() -> None:
    detector = RegimeMismatchDetector()
    for regime in MarketRegime:
        assert detector.check_mismatch(StrategyType.CUSTOM, regime) is None


def test_display_name_appears_in_message_when_given() -> None:
    detector = RegimeMismatchDetector()
    finding = detector.check_mismatch(
        StrategyType.SMA_CROSS,
        MarketRegime.RANGING,
        strategy_name="My SMA Strategy",
    )
    assert finding is not None
    assert "My SMA Strategy" in finding.message


# --- severity grading ------------------------------------------------------------------


def test_weak_trend_during_range_mismatch_grades_dangerous() -> None:
    detector = RegimeMismatchDetector()
    finding = detector.check_mismatch(
        StrategyType.SMA_CROSS, MarketRegime.RANGING, trend_strength=0.2
    )
    assert finding is not None
    assert finding.severity == RiskLevel.DANGEROUS


def test_moderate_trend_during_range_mismatch_grades_restricted() -> None:
    detector = RegimeMismatchDetector()
    finding = detector.check_mismatch(
        StrategyType.SMA_CROSS, MarketRegime.RANGING, trend_strength=0.5
    )
    assert finding is not None
    assert finding.severity == RiskLevel.RESTRICTED


def test_strong_trend_against_mean_reversion_grades_dangerous() -> None:
    detector = RegimeMismatchDetector()
    finding = detector.check_mismatch(
        StrategyType.RSI_REVERSAL, MarketRegime.TRENDING, trend_strength=0.8
    )
    assert finding is not None
    assert finding.severity == RiskLevel.DANGEROUS


def test_extreme_volatility_grades_dangerous() -> None:
    detector = RegimeMismatchDetector()
    finding = detector.check_mismatch(
        StrategyType.SMA_CROSS, MarketRegime.VOLATILE, volatility=0.9
    )
    assert finding is not None
    assert finding.severity == RiskLevel.DANGEROUS


# --- volatility gate ----------------------------------------------------------------------


def test_macd_survives_high_volatility_check() -> None:
    detector = RegimeMismatchDetector()
    assert detector.check_volatility_mismatch(StrategyType.MACD, volatility=0.95) is None


def test_non_volatile_strategy_flagged_above_threshold() -> None:
    detector = RegimeMismatchDetector()
    finding = detector.check_volatility_mismatch(StrategyType.RSI_REVERSAL, volatility=0.9)
    assert finding is not None
    assert "volatility" in finding.message.lower()


def test_mid_level_volatility_passes_default_gate() -> None:
    detector = RegimeMismatchDetector()
    assert detector.check_volatility_mismatch(StrategyType.RSI_REVERSAL, volatility=0.5) is None


def test_lowered_volatility_ceiling_trips_earlier() -> None:
    detector = RegimeMismatchDetector()
    finding = detector.check_volatility_mismatch(
        StrategyType.SMA_CROSS, volatility=0.6, max_volatility=0.5
    )
    assert finding is not None


# --- custom compatibility override -------------------------------------------------------------


def test_caller_matrix_can_whitelist_a_pairing() -> None:
    override = {
        StrategyType.SMA_CROSS: {MarketRegime.RANGING, MarketRegime.TRENDING},
    }
    detector = RegimeMismatchDetector(custom_compat=override)
    # Ranging now whitelisted under the caller's matrix.
    assert detector.check_mismatch(StrategyType.SMA_CROSS, MarketRegime.RANGING) is None

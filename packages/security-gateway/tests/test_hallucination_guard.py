"""Behavior locks for financial hallucination detection: unverified claim
scanning, investment promises, secret redaction, and the golden snapshot."""

from __future__ import annotations

import pytest

from selene_space_security.guardrails.hallucination import HallucinationDetector

# --- unverified claims -------------------------------------------------------


def test_price_claim_without_tool_backing_warns() -> None:
    detector = HallucinationDetector()
    scan = detector.scan_text_for_unverified_claims(
        "BTC is currently at $100,000 and ETH at $5,000.",
        tool_results=[],
    )
    assert scan.triggered is True
    assert any("未经工具验证" in w for w in scan.warnings)


def test_two_stats_without_support_still_flagged() -> None:
    detector = HallucinationDetector()
    scan = detector.scan_text_for_unverified_claims(
        "The Sharpe ratio is 2.5 and drawdown is 15%.",
        tool_results=[],
    )
    assert scan.triggered is True
    assert len(scan.warnings) >= 1


# --- verified claims ------------------------------------------------------------


def test_ticker_backed_price_claim_is_clean() -> None:
    detector = HallucinationDetector()
    scan = detector.scan_text_for_unverified_claims(
        "BTC is currently at $67,234.",
        tool_results=[{"output": "BTC/USDT price: 67234.0", "tool": "market_ticker"}],
    )
    assert scan.triggered is False
    assert scan.warnings == []


def test_backtest_backed_percentage_claim_is_clean() -> None:
    detector = HallucinationDetector()
    scan = detector.scan_text_for_unverified_claims(
        "Strategy returned 23.5% annualized.",
        tool_results=[{"output": "Return: 23.5% annualized", "tool": "backtest_run"}],
    )
    assert scan.triggered is False


# --- investment promises ----------------------------------------------------------


PROMISE_PHRASES = [
    "这个策略保证盈利",
    "这是一个零风险的投资",
    "买这个必涨",
    "This strategy has guaranteed returns",
    "Risk-free opportunity",
    "You can't lose with this approach",
    "Sure profit strategy",
    "稳赚不赔",
]


@pytest.mark.parametrize("phrase", PROMISE_PHRASES)
def test_each_promise_phrase_appends_risk_disclaimer(phrase: str) -> None:
    detector = HallucinationDetector()
    scan = detector.scan_for_investment_promises(phrase)
    assert scan.triggered is True
    assert any("投资有风险" in w for w in scan.warnings)


def test_statistical_summary_is_not_a_promise() -> None:
    detector = HallucinationDetector()
    scan = detector.scan_for_investment_promises(
        "Based on historical data, this strategy shows a Sharpe ratio of 1.5."
    )
    assert scan.triggered is False
    assert scan.warnings == []


# --- secret redaction ----------------------------------------------------------------


def test_api_key_removed_from_output() -> None:
    detector = HallucinationDetector()
    cleaned = detector.redact_secrets_in_output("Your API key is sk-abc123456789012345678")
    assert "sk-abc123456789012345678" not in cleaned


def test_bearer_jwt_removed_from_output() -> None:
    detector = HallucinationDetector()
    text = (
        "Authorization: Bearer "
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in detector.redact_secrets_in_output(text)


def test_text_without_secrets_untouched() -> None:
    detector = HallucinationDetector()
    original = "BTC price is $67,234. No secrets here."
    assert detector.redact_secrets_in_output(original) == original


# --- golden response snapshot (regression guard) -----------------------------------------


GOLDEN_INPUT = "BTC 当前 $100,000，这个策略保证盈利，API key: sk-test1234567890abcdef"


def test_golden_response_snapshot() -> None:
    """Fixed input/output snapshot to detect prompt or detection logic drift."""
    detector = HallucinationDetector()
    emitted, scan = detector.scan_output(GOLDEN_INPUT, tool_results=[])

    # Secret must be redacted
    assert "sk-test1234567890abcdef" not in emitted

    # Investment promise disclaimer must be present
    assert "投资有风险" in emitted

    # Unverified claim warnings are logged internally only, not in output
    assert "未经工具验证" not in emitted

    # Overall scan should have triggered (internally)
    assert scan.triggered is True


# --- property: output never contains an unredacted secret ---------------------------------

LEAK_CANDIDATES = [
    "sk-abcdef1234567890abcdef",
    "ghp_abcdef1234567890abcdef1234567890",
    "AKIA1234567890123456",
]


@pytest.mark.parametrize("secret", LEAK_CANDIDATES)
def test_no_secret_shape_survives_redaction(secret: str) -> None:
    detector = HallucinationDetector()
    cleaned = detector.redact_secrets_in_output(f"Here is a result. The key is {secret}. Thanks!")
    assert secret not in cleaned


# --- scan_output integration -----------------------------------------------------------------


def test_scan_output_merges_all_checks_for_dirty_text() -> None:
    detector = HallucinationDetector()
    emitted, scan = detector.scan_output("BTC is $99,999. This strategy guarantees profit.", tool_results=[])

    assert scan.triggered is True
    # Unverified claims are logged internally, not shown to users
    assert "未经工具验证" not in emitted
    # Investment promise disclaimer IS shown to users
    assert "投资有风险" in emitted


def test_scan_output_leaves_clean_text_alone() -> None:
    detector = HallucinationDetector()
    original = "Based on the data retrieved, the analysis shows mixed signals."
    emitted, scan = detector.scan_output(original, tool_results=[])

    assert scan.triggered is False
    assert emitted == original

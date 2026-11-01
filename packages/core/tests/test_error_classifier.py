"""Resilience locks for selene_space_core.resilience.error_classifier —
exception-type and message-text routing into categories."""

import pytest

from selene_space_core.resilience.error_classifier import ErrorCategory, classify_error

TYPE_CASES = [
    (TimeoutError("timed out"), ErrorCategory.TIMEOUT),
    (TimeoutError(), ErrorCategory.TIMEOUT),
    (ConnectionError("refused"), ErrorCategory.NETWORK),
    (OSError("dns failure"), ErrorCategory.NETWORK),
]

TEXT_CASES = [
    ("HTTP 429 Too Many Requests", ErrorCategory.RATE_LIMIT),
    ("rate limit exceeded", ErrorCategory.RATE_LIMIT),
    ("HTTP 401 Unauthorized", ErrorCategory.AUTH),
    ("403 Forbidden", ErrorCategory.AUTH),
    ("Invalid API key", ErrorCategory.AUTH),
    ("402 Payment Required", ErrorCategory.BILLING),
    ("quota exceeded", ErrorCategory.BILLING),
]


@pytest.mark.parametrize(("exc", "expected"), TYPE_CASES)
def test_exception_type_routing(exc, expected):
    assert classify_error(exc) == expected


@pytest.mark.parametrize(("text", "expected"), TEXT_CASES)
def test_message_text_routing(text, expected):
    assert classify_error(Exception(text)) == expected


def test_unrecognized_error_lands_in_unknown():
    assert classify_error(ValueError("something else")) == ErrorCategory.UNKNOWN

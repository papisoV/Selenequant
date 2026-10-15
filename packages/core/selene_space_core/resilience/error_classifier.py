"""Map exceptions to coarse categories so callers can decide retry/failover."""

from __future__ import annotations

import enum


class ErrorCategory(enum.Enum):
    TIMEOUT = "timeout"
    NETWORK = "network"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    BILLING = "billing"
    UNKNOWN = "unknown"


_TIMEOUT_MARKERS = ("timeout", "timed out")
_NETWORK_MARKERS = ("connection", "connect", "dns", "network")
_RATE_LIMIT_MARKERS = ("429", "rate limit")
_AUTH_MARKERS = ("401", "403", "unauthorized", "forbidden", "invalid api key")
_BILLING_MARKERS = ("402", "payment required", "quota exceeded")


def classify_error(exc: BaseException) -> ErrorCategory:
    if isinstance(exc, TimeoutError):
        return ErrorCategory.TIMEOUT

    if isinstance(exc, (ConnectionError, OSError)):
        return ErrorCategory.NETWORK

    text = str(exc).lower()
    if any(m in text for m in _RATE_LIMIT_MARKERS):
        return ErrorCategory.RATE_LIMIT
    if any(m in text for m in _AUTH_MARKERS):
        return ErrorCategory.AUTH
    if any(m in text for m in _BILLING_MARKERS):
        return ErrorCategory.BILLING
    if any(m in text for m in _TIMEOUT_MARKERS):
        return ErrorCategory.TIMEOUT
    if any(m in text for m in _NETWORK_MARKERS):
        return ErrorCategory.NETWORK

    return ErrorCategory.UNKNOWN

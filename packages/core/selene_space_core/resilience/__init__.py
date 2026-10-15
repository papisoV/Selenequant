"""Failure-handling toolkit for outbound work: pause scheduling, error
triage, bounded retries, circuit breaking, and the thread-isolated HTTP
gateway.

Public surface (frozen): BackoffPolicy, CircuitBreaker, CircuitOpenError,
CircuitState, ErrorCategory, OutboundGateway, classify_error, retry_async.
"""

from selene_space_core.resilience.backoff import BackoffPolicy
from selene_space_core.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)
from selene_space_core.resilience.error_classifier import ErrorCategory, classify_error
from selene_space_core.resilience.outbound_gateway import OutboundGateway
from selene_space_core.resilience.retry import retry_async

__all__ = [
    "BackoffPolicy",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "ErrorCategory",
    "OutboundGateway",
    "classify_error",
    "retry_async",
]

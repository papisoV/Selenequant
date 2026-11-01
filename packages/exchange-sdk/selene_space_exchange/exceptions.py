"""Exception types raised across the exchange SDK.

Every failure here subclasses
:class:`selene_space_types.errors.ExchangeError`, so a caller can either
discriminate on the specific class or fall back to the shared base.
Keyword context accepted at raise time lands both on the instance as plain
attributes and inside ``details`` so structured logging sees one shape.
"""

from __future__ import annotations

from typing import Any

from selene_space_types.errors import ExchangeError


def _bundle(
    details: dict[str, Any] | None,
    **extras: Any,
) -> dict[str, Any] | None:
    """Merge explicit extras with caller-supplied details into one mapping."""
    merged = {k: v for k, v in extras.items() if v is not None}
    if details:
        merged.update(details)
    return merged or None


class WebSocketConnectionError(ExchangeError):
    """Dialing or keeping a stream socket alive failed."""

    def __init__(
        self,
        message: str,
        *,
        exchange: str | None = None,
        url: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=_bundle(details, exchange=exchange, url=url))
        self.exchange = exchange
        self.url = url


class WebSocketSubscriptionError(ExchangeError):
    """A subscribe/unsubscribe request came back rejected."""

    def __init__(
        self,
        message: str,
        *,
        streams: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=_bundle(details, streams=streams))
        self.streams = streams


class StallTimeoutError(ExchangeError):
    """A feed went quiet for longer than its watchdog allows."""

    def __init__(
        self,
        message: str = "WebSocket stall detected: no data within timeout",
        *,
        idle_s: float | None = None,
        timeout_s: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message, details=_bundle(details, idle_s=idle_s, timeout_s=timeout_s)
        )
        self.idle_s = idle_s
        self.timeout_s = timeout_s


class SequenceGapError(ExchangeError):
    """Orderbook deltas arrived with a hole in their sequence numbers."""

    def __init__(
        self,
        message: str = "Sequence gap detected in L2 orderbook",
        *,
        expected: int | None = None,
        received: int | None = None,
        symbol: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            details=_bundle(details, expected=expected, received=received, symbol=symbol),
        )
        self.expected = expected
        self.received = received
        self.symbol = symbol


class SnapshotRecoveryError(ExchangeError):
    """Re-syncing an orderbook from a REST snapshot did not complete."""

    def __init__(
        self,
        message: str = "Failed to recover L2 orderbook snapshot via REST",
        *,
        symbol: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=_bundle(details, symbol=symbol))
        self.symbol = symbol


class ExchangeAPIError(ExchangeError):
    """A REST call returned a non-success answer from the venue."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        exchange: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            details=_bundle(details, status_code=status_code, exchange=exchange),
        )
        self.status_code = status_code
        self.exchange = exchange


class AuthenticationError(ExchangeAPIError):
    """The venue turned down our credentials or signature."""


class RateLimitExceededError(ExchangeAPIError):
    """The venue throttled us (weight budget or HTTP 429)."""


class InsufficientBalanceError(ExchangeAPIError):
    """Funds on the account cannot cover the requested size."""


class OrderNotFoundError(ExchangeAPIError):
    """No order matches the id we asked about."""


class OrderRejectedError(ExchangeAPIError):
    """The venue refused the order outright."""


class InvalidOrderError(ExchangeAPIError):
    """Local pre-flight checks found the order parameters malformed."""


class PreTradeRiskBlockError(ExchangeError):
    """A local risk gate vetoed the order before it left the process."""

    def __init__(
        self,
        message: str,
        *,
        blocked_by: str | None = None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message, details=_bundle(details, blocked_by=blocked_by, reason=reason)
        )
        self.blocked_by = blocked_by
        self.reason = reason

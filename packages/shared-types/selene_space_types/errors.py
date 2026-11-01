"""Domain failure taxonomy shared by every SeleneQuant service.

Two halves live here. The *raising* half: business code throws
:class:`SeleneSpaceError` (usually one of its thin subclasses) tagged with an
:class:`ErrorCode` saying what category of thing went wrong. The *catching*
half: transport layers translate that code into an outbound representation --
HTTP status via ``ERROR_CODE_HTTP_STATUS``, JSON payload via ``to_dict()``.
Extending the taxonomy therefore means three steps: declare a code, register
its HTTP status, and -- when call sites would benefit -- add a subclass that
pins it.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import ClassVar


class ErrorCode(str, Enum):
    """Machine-readable failure identifiers with stable wire format.

    Each member serializes to its own spelling (``ErrorCode.CONFLICT.value``
    is ``"CONFLICT"``); log fields, database columns, and API responses all
    persist that spelling, so renaming a member would corrupt historical data.
    Deriving values from names via ``auto()`` keeps the two in lockstep by
    construction instead of by convention.
    """

    @staticmethod
    def _generate_next_value_(
        name: str, start: int, count: int, last_values: list[str]
    ) -> str:
        return name

    VALIDATION_ERROR = auto()
    NOT_FOUND = auto()
    RISK_DENIED = auto()
    RATE_LIMITED = auto()
    EXCHANGE_ERROR = auto()
    INTERNAL_ERROR = auto()
    PERMISSION_DENIED = auto()
    CONFLICT = auto()
    INVALID_PARAMETER = auto()
    INVALID_STRATEGY = auto()
    SERVICE_UNAVAILABLE = auto()
    AUTHENTICATION_ERROR = auto()


# Rows of ``(code, http status)`` describing how each failure category maps
# onto an HTTP response. Row order carries no meaning; lookups go through the
# dictionary assembled below.
_STATUS_ROWS: tuple[tuple[ErrorCode, int], ...] = (
    (ErrorCode.AUTHENTICATION_ERROR, 401),
    (ErrorCode.SERVICE_UNAVAILABLE, 503),
    (ErrorCode.INVALID_STRATEGY, 400),
    (ErrorCode.INVALID_PARAMETER, 400),
    (ErrorCode.CONFLICT, 409),
    (ErrorCode.PERMISSION_DENIED, 403),
    (ErrorCode.INTERNAL_ERROR, 500),
    (ErrorCode.EXCHANGE_ERROR, 502),
    (ErrorCode.RATE_LIMITED, 429),
    (ErrorCode.RISK_DENIED, 403),
    (ErrorCode.NOT_FOUND, 404),
    (ErrorCode.VALIDATION_ERROR, 400),
)

ERROR_CODE_HTTP_STATUS: dict[ErrorCode, int] = dict(_STATUS_ROWS)


class SeleneSpaceError(Exception):
    """Root of the failure hierarchy.

    An instance bundles three facts: ``code`` (the machine classifier),
    ``message`` (human summary -- also what ``str(exc)`` returns, thanks to
    forwarding into ``Exception.args``), and optionally ``details`` (a
    structured mapping of extras: offending field names, ids, upstream
    errors). The ``http_status`` attribute is derived from the code rather
    than supplied by callers.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict | None = None,
    ) -> None:
        if code not in ERROR_CODE_HTTP_STATUS:
            raise ValueError(f"error code has no registered HTTP status: {code!r}")
        super().__init__(message)
        self.details = details
        self.message = message
        self.code = code
        self.http_status = ERROR_CODE_HTTP_STATUS[code]

    def to_dict(self) -> dict:
        """Produce the ``error`` fragment of an API response body.

        When no details were attached, the key is left out altogether rather
        than emitted as null, keeping response schemas tight.
        """
        if self.details is None:
            return {"code": self.code.value, "message": self.message}
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }


class _FixedCodeFailure(SeleneSpaceError):
    """Shared plumbing for exceptions whose code is fixed per class.

    Concrete subclasses only declare ``_fixed_code``; the constructor forwards
    it to the root initializer together with the caller-supplied message and
    details.
    """

    _fixed_code: ClassVar[ErrorCode]

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(self._fixed_code, message, details)


class ValidationError(_FixedCodeFailure):
    """Payload failed schema or semantic checks before business logic ran."""

    _fixed_code = ErrorCode.VALIDATION_ERROR


class NotFoundError(_FixedCodeFailure):
    """The addressed entity could not be located."""

    _fixed_code = ErrorCode.NOT_FOUND


class ExchangeError(_FixedCodeFailure):
    """Interaction with an upstream venue failed or stalled."""

    _fixed_code = ErrorCode.EXCHANGE_ERROR


class RiskDeniedError(_FixedCodeFailure):
    """A pre-trade risk gate vetoed the requested action."""

    _fixed_code = ErrorCode.RISK_DENIED


class RateLimitedError(_FixedCodeFailure):
    """The caller exhausted a throttling budget."""

    _fixed_code = ErrorCode.RATE_LIMITED


class InternalError(_FixedCodeFailure):
    """Something broke on our side; no narrower classification applies."""

    _fixed_code = ErrorCode.INTERNAL_ERROR

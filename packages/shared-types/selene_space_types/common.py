"""Shared primitive types and the generic API response envelope.

These are the building blocks every other module leans on: string aliases for
instrument codes and millisecond timestamps, pagination metadata, error
payloads, and the uniform success/failure wrapper used across HTTP surfaces.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

# Trading pair / instrument code, e.g. "BTC/USDT". A plain alias so consumers
# assign raw strings without any coercion step.
Symbol = str

# Milliseconds since Unix epoch — the canonical timestamp representation used
# throughout the platform (int(time.time() * 1000)).
Timestamp = int

PayloadT = TypeVar("PayloadT")


class Pagination(BaseModel):
    """Offset-based paging descriptor for list endpoints."""

    offset: int = 0
    limit: int = 50
    total: int = 0


class ErrorInfo(BaseModel):
    """Machine-readable error payload carried inside failed API responses."""

    code: str
    message: str
    details: dict | None = None


class ResponseMeta(BaseModel):
    """Optional envelope metadata: correlation id and paging info."""

    request_id: str | None = None
    pagination: Pagination | None = None


class APIResponse(BaseModel, Generic[PayloadT]):
    """Uniform response envelope.

    Success shape carries ``data`` with ``error=None``; failure shape carries
    a populated ``error`` (and typically ``data=None``).
    """

    data: PayloadT | None = None
    meta: ResponseMeta | None = None
    error: ErrorInfo | None = None

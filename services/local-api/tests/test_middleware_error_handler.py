"""Error-handler middleware contract, verified against the live ASGI stack.

Every FastAPI response in SeleneQuant carries one of two shapes:

    success -> {"data": <payload>, "meta": {"request_id": ...}}
    failure -> {"data": None,
                "error": {"code": ..., "message": ..., "details": ...},
                "meta": {"request_id": ...}}

These tests pin the failure branch: domain errors map onto their HTTP status,
pydantic validation failures carry structured ``details``, unexpected crashes
are masked behind a generic 500 (never leaking the exception text), and the
request-id middleware still stamps every failure envelope.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient


class _Envelope:
    """Typed reader over the failure envelope returned by the API.

    Wrapping the raw JSON here keeps each test focused on the contract it
    verifies instead of repeating dict-navigation chains, and gives the
    middleware behaviour a single named vocabulary: ``slot`` for data,
    ``fault`` for the error object, ``trace`` for request metadata.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    @property
    def slot(self) -> Any:
        """The ``data`` member: null on every failure response."""
        return self._payload.get("data")

    @property
    def fault(self) -> dict[str, Any]:
        """The ``error`` object: code / message / details triple."""
        return self._payload.get("error") or {}

    @property
    def fault_kind(self) -> str:
        """Machine-readable error code, e.g. NOT_FOUND."""
        return str(self.fault.get("code", ""))

    @property
    def fault_text(self) -> str:
        """Human-readable error message."""
        return str(self.fault.get("message", ""))

    @property
    def trace(self) -> dict[str, Any]:
        """Request metadata echoed by the request-id middleware."""
        return self._payload.get("meta") or {}


async def _exchange(
    verb: str,
    url: str,
    *,
    app: Any = None,
    tolerate_crash: bool = False,
    body: dict | None = None,
) -> Any:
    """Send one request through `app` (fresh when omitted); returns response.

    ``tolerate_crash`` lets the ASGI transport propagate unhandled exceptions
    instead of httpx raising them, which is exactly what the 500-masking test
    needs the middleware to intercept.
    """
    opts = {"raise_app_exceptions": False} if tolerate_crash else {}
    target = app if app is not None else create_app()
    async with AsyncClient(transport=ASGITransport(app=target, **opts), base_url="http://test") as c:
        if body is None:
            return await getattr(c, verb)(url)
        return await getattr(c, verb)(url, json=body)


@pytest.mark.asyncio
async def test_not_found_error_is_typed():
    """Domain NotFoundError surfaces as 404 inside the unified envelope.

    Fetching a strategy id that was never created exercises the typed-error
    path: the handler must translate SeleneSpaceError subclasses into their
    HTTP counterparts while keeping ``data`` null and echoing the offending
    identifier back in the message.
    """
    reply = await _exchange("get", "/api/v1/strategies/nonexistent")
    assert reply.status_code == 404
    env = _Envelope(reply.json())
    assert env.slot is None
    assert env.fault_kind == "NOT_FOUND"
    assert "nonexistent" in env.fault_text


@pytest.mark.asyncio
async def test_service_unavailable_error_is_typed():
    """A missing dependency reports SERVICE_UNAVAILABLE with status 503.

    The market service resolves to None outside of app lifespan, so hitting
    any market endpoint forces ``_require_market_service`` to raise — this is
    the canonical check that dependency-absence is reported as 503 rather
    than crashing the worker or masquerading as a 404.
    """
    reply = await _exchange("get", "/api/v1/markets")
    assert reply.status_code == 503
    assert _Envelope(reply.json()).fault_kind == "SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_validation_errors_carry_details():
    """Pydantic rejections become 422 envelopes with structured details.

    Posting an empty body to the strategy factory omits every required field;
    the response must carry the VALIDATION_ERROR code and its ``details``
    must include an ``errors`` collection enumerating the violated fields.
    """
    reply = await _exchange("post", "/api/v1/strategies", body={})
    assert reply.status_code == 422
    fault = _Envelope(reply.json()).fault
    assert fault["code"] == "VALIDATION_ERROR"
    assert "errors" in fault["details"]


@pytest.mark.asyncio
async def test_crash_is_masked_as_internal_error():
    """Unhandled exceptions collapse into a leak-free generic 500.

    A bomb router raises RuntimeError mid-request; the middleware must catch
    what escapes the endpoint layer and answer with the fixed INTERNAL_ERROR
    wording. The original exception text ('something broke internally') must
    never appear anywhere in the outbound message.
    """
    from fastapi import APIRouter

    app = create_app()
    bomb_router = APIRouter()

    @bomb_router.get("/api/v1/test-crash")
    async def detonate():
        raise RuntimeError("something broke internally")

    app.include_router(bomb_router)

    reply = await _exchange("get", "/api/v1/test-crash", app=app, tolerate_crash=True)
    assert reply.status_code == 500
    env = _Envelope(reply.json())
    assert env.fault_kind == "INTERNAL_ERROR"
    assert env.fault_text == "An internal error occurred"
    # Internal error details should NOT be leaked.
    assert "something broke" not in env.fault_text


@pytest.mark.asyncio
async def test_error_envelope_includes_request_meta():
    """Failure responses still carry the request-id stamped by middleware.

    Error paths bypass the success serializer, so this guards that the
    meta{request_id} block is attached on the failure branch too — clients
    correlating logs need it regardless of outcome.
    """
    reply = await _exchange("get", "/api/v1/strategies/nonexistent")
    env = _Envelope(reply.json())
    assert "meta" in env._payload
    assert "request_id" in env.trace

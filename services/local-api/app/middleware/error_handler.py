"""Unified error handling middleware.

Catches all exceptions and returns consistent JSON error responses.
Integrates with SeleneSpaceError hierarchy from shared-types.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from selene_space_types.errors import (
    ERROR_CODE_HTTP_STATUS,
    ErrorCode,
    SeleneSpaceError,
)

logger = structlog.get_logger(__name__)

#: 统一错误响应里错误体的三个键。
_CODE_FIELD = "code"
_MESSAGE_FIELD = "message"
_DETAILS_FIELD = "details"


def _request_id(request: Request) -> str | None:
    """请求上下文里的 request_id(未绑定则为 None)。"""
    return getattr(request.state, "request_id", None)


def _error_body(
    request: Request, code: str, message: str, details: object = None
) -> dict:
    """拼出统一的 {data, meta, error} 响应体。"""
    error: dict = {_CODE_FIELD: code, _MESSAGE_FIELD: message}
    if details is not None:
        error[_DETAILS_FIELD] = details
    return {"data": None, "meta": {"request_id": _request_id(request)}, "error": error}


def _envelope(request: Request, error: dict) -> dict:
    """把现成的 error 片段包进统一外壳(不动片段本身)。"""
    return {"data": None, "meta": {"request_id": _request_id(request)}, "error": error}


def install_error_handlers(app: FastAPI) -> None:
    """Register exception handlers on the FastAPI application."""

    @app.exception_handler(SeleneSpaceError)
    async def _handle_selenespace_error(
        request: Request, exc: SeleneSpaceError
    ) -> JSONResponse:
        """Map SeleneSpaceError to the correct HTTP status with unified body."""
        status = ERROR_CODE_HTTP_STATUS.get(exc.code, exc.http_status)
        logger.warning(
            "selenespace_error",
            code=exc.code.value,
            message=exc.message,
            status=status,
        )
        # 错误片段直接取 exc.to_dict(), 不重建 —— 其 details 原样透传。
        return JSONResponse(
            status_code=status, content=_envelope(request, exc.to_dict())
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Pydantic / FastAPI validation errors → 422."""
        errors = [
            {
                "loc": list(item.get("loc", [])),
                "msg": item.get("msg", ""),
                "type": item.get("type", ""),
            }
            for item in exc.errors()
        ]
        body = _error_body(
            request,
            ErrorCode.VALIDATION_ERROR.value,
            "Request validation failed",
            {"errors": errors},
        )
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(Exception)
    async def _handle_unknown_error(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all for unhandled exceptions → 500.

        Internal details are logged but NOT exposed in the response.
        """
        logger.exception("unhandled_error", exc_type=type(exc).__name__)
        body = _error_body(
            request, ErrorCode.INTERNAL_ERROR.value, "An internal error occurred"
        )
        return JSONResponse(status_code=500, content=body)

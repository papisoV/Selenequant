"""Request ID middleware (pure ASGI for minimal overhead).

Assigns a UUID to each incoming request, binds it to structlog context,
stores it on ``request.state``, and adds it as ``X-Request-ID`` response header.

If the request already carries an ``X-Request-ID`` header, that value is reused.
"""

from __future__ import annotations

import uuid
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from selene_space_core.logging import bind_request_id

_HEADER = b"x-request-id"
#: 只在这两种连接类型上工作; 其余(lifespan 等)原样透传。
_APPLICABLE_SCOPES = frozenset({"http", "websocket"})


def _incoming_request_id(scope: Scope) -> str:
    """复用来访请求自带的 X-Request-ID; 没有就现生成一个。"""
    headers = dict(scope.get("headers", []))
    provided = headers.get(_HEADER, b"").decode()
    return provided or uuid.uuid4().hex


class RequestIDMiddleware:
    """Pure ASGI middleware — avoids BaseHTTPMiddleware thread overhead."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _with_response_header(send: Send, request_id: str) -> Any:
        """包一层 send, 在响应头里补上 X-Request-ID。"""
        encoded = request_id.encode()

        async def send_with_id(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((_HEADER, encoded))
                message["headers"] = headers
            await send(message)

        return send_with_id

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in _APPLICABLE_SCOPES:
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        bind_request_id(request_id)

        # websocket 没有响应头可挂, 只做上下文绑定。
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, self._with_response_header(send, request_id))

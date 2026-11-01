"""JWT auth + redemption-code entitlement gate (monetize Phase 1.3).

Trust model:
- ``SELENE_BILLING_MODE`` unset/0 → module inert; all dependencies pass through.
  Localhost-trust behavior in ``require_user``/``optional_user`` is unchanged.
- ``SELENE_BILLING_MODE=1`` → remote requests must carry a valid JWT; paid
  endpoints additionally require an active redemption-code entitlement (402).

JWT secret: ``SELENE_JWT_SECRET`` (required in billing mode; refuse to start
without it — fail-closed, consistent with the auth-disabled production guard).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.dependencies import AuthenticatedUser, _bearer_scheme

JWT_ALGORITHM = "HS256"
JWT_TTL_HOURS = 72

# Endpoints reachable without login while billing mode is on. Matched as
# (method, path-suffix) against request.scope; prefixes cover routers.
PUBLIC_PATH_PREFIXES = (
    "/api/v1/health",
    "/api/v1/auth",
    "/api/v1/docs",
    "/api/v1/redoc",
    "/api/v1/openapi.json",
    "/api/v1/settings/llm",
)

_billing_repo: Any = None
_jwt_secret: str | None = None


def billing_mode_enabled() -> bool:
    return os.environ.get("SELENE_BILLING_MODE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def require_jwt_secret() -> str:
    """Return the configured secret; raise in billing mode if missing."""
    global _jwt_secret
    if _jwt_secret:
        return _jwt_secret
    secret = os.environ.get("SELENE_JWT_SECRET", "").strip()
    if not secret and billing_mode_enabled():
        raise RuntimeError(
            "SELENE_BILLING_MODE=1 requires SELENE_JWT_SECRET (fail-closed)"
        )
    _jwt_secret = secret
    return _jwt_secret


def set_billing_repo(repo: Any) -> None:
    global _billing_repo
    _billing_repo = repo


def get_billing_repo() -> Any:
    return _billing_repo


def create_access_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(hours=JWT_TTL_HOURS),
    }
    return pyjwt.encode(payload, require_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    return pyjwt.decode(token, require_jwt_secret(), algorithms=[JWT_ALGORITHM])


async def billing_auth_guard(
    request: Request,
    creds: Any = Depends(_bearer_scheme),
) -> None:
    """Dependency form of the billing gate (route-level use only).

    Do NOT mount this via app-level ``dependencies=``: FastAPI 0.138.x cannot
    solve Request-param dependencies in websocket scope, so every
    ``/api/v1/ws/*`` handshake 500s — mount BillingGateMiddleware instead.
    """
    if not billing_mode_enabled():
        return
    path = request.url.path
    if any(path.startswith(p) for p in PUBLIC_PATH_PREFIXES):
        return
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = decode_token(creds.credentials)
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    request.state.user_id = payload.get("sub") or ""


async def require_billing_user(
    request: Request,
    creds: Any = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    """Identity dependency under billing mode: JWT required, user attached.
    Falls back to local/anonymous identity when billing mode is off."""
    if not billing_mode_enabled():
        client = request.client
        if client and client.host in (
            "127.0.0.1",
            "::1",
            "localhost",
            "testclient",
        ):
            return AuthenticatedUser(id="local", role="admin")
        return AuthenticatedUser(id="anonymous", role="anonymous")

    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = decode_token(creds.credentials)
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return AuthenticatedUser(id=str(payload.get("sub") or ""), role="user")


async def require_active_entitlement(
    request: Request,
    user: AuthenticatedUser = Depends(require_billing_user),
) -> AuthenticatedUser:
    """Paid-endpoint dependency: on top of auth, require an unexpired code
    binding. 402 when billing mode is on and the user has no entitlement.
    Billing-mode-off passes through (local trust model unchanged)."""
    if not billing_mode_enabled():
        return user
    repo = get_billing_repo()
    if repo is None:
        # Fail-closed: billing on but repo not wired → deny.
        raise HTTPException(status_code=402, detail="No active subscription")
    if not await repo.has_active_entitlement(user.id):
        raise HTTPException(status_code=402, detail="No active subscription")
    return user


class BillingGateMiddleware:
    """Pure ASGI replacement for the old app-level billing_auth_guard.

    Why: FastAPI 0.138.x cannot solve Request-param dependencies in websocket
    scope — mounting the guard via ``FastAPI(dependencies=[...])`` made every
    ``/api/v1/ws/*`` handshake 500 (HTTPBearer.__call__ missing 'request').
    Pure ASGI sees scope["type"] directly, so it gates http normally and
    handles websocket explicitly: billing mode rejects the handshake (401),
    local mode lets it through unchanged.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        stype = scope.get("type")
        if stype not in ("http", "websocket") or not billing_mode_enabled():
            await self.app(scope, receive, send)
            return

        if stype == "websocket":
            # Billing posture has no localhost bypass (same as http path).
            path = scope.get("path", "")
            if any(path.startswith(p) for p in PUBLIC_PATH_PREFIXES):
                await self.app(scope, receive, send)
                return
            headers = dict(scope.get("headers", []))
            token = headers.get(b"authorization", b"").decode()
            ok = False
            if token.lower().startswith("bearer "):
                try:
                    payload = decode_token(token[7:].strip())
                    scope.setdefault("state", {})["user_id"] = payload.get("sub") or ""
                    ok = True
                except pyjwt.PyJWTError:
                    ok = False
            if not ok:
                await _ws_reject(scope, receive, send, status=1008)
                return
            await self.app(scope, receive, send)
            return

        # http scope
        path = scope.get("path", "")
        if any(path.startswith(p) for p in PUBLIC_PATH_PREFIXES):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        user_id = ""
        ok = False
        if auth.lower().startswith("bearer "):
            try:
                payload = decode_token(auth[7:].strip())
                user_id = payload.get("sub") or ""
                ok = True
            except pyjwt.PyJWTError:
                ok = False
        if not ok:
            await _http_reject(send, status=401, detail="Authentication required")
            return
        scope.setdefault("state", {})["user_id"] = user_id
        await self.app(scope, receive, send)


async def _ws_reject(scope: Scope, receive: Receive, send: Send, status: int) -> None:
    """Close a websocket handshake before accept with an explicit close code."""
    await send(
        {"type": "websocket.close", "code": status, "reason": "Authentication required"}
    )


async def _http_reject(send: Send, status: int, detail: str) -> None:
    body = f'{{"detail":"{detail}"}}'.encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})

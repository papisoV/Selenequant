"""Rate-limit buckets: authenticated traffic meters per user, auth per IP.

Behind the deploy reverse proxy (Caddy -> web -> api, or Caddy -> api) every
request arrives from a single address. Keying buckets on the client IP would
put all users in one bucket, so one user's page load (~91 requests) would 429
everybody else. BillingGateMiddleware resolves the identity into
scope["state"]["user_id"]; the limiter now meters on that.

Auth endpoints keep IP bucketing: they are the surface an unauthenticated
caller can reach, and their tighter quota exists to blunt credential stuffing
— a per-user bucket would hand an attacker a fresh one per guess.

Reverse-verify: make _bucket_key return f"ip:{ip}" unconditionally and
test_users_have_independent_buckets goes red.
"""

from __future__ import annotations

import pytest
from app.middleware.rate_limit import (
    _AUTH_PATH_PREFIXES,
    _AUTH_QUOTA,
    _GLOBAL_QUOTA,
    RateLimitMiddleware,
)


async def _ok_app(scope, receive, send):  # noqa: ANN001 - ASGI signature
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


def _scope(path: str, *, user_id: str | None = None, ip: str = "10.0.0.1") -> dict:
    scope: dict = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "client": (ip, 41234),
    }
    if user_id is not None:
        scope["state"] = {"user_id": user_id}
    return scope


async def _status(mw: RateLimitMiddleware, scope: dict) -> int:
    sent: list[dict] = []

    async def receive():  # noqa: ANN202 - ASGI signature
        return {"type": "http.request"}

    async def send(message):  # noqa: ANN001, ANN202 - ASGI signature
        sent.append(message)

    await mw(scope, receive, send)
    # ASGI messages are dicts of Any; the status is an int by protocol.
    return int(sent[0]["status"])


async def _hit(mw: RateLimitMiddleware, n: int, **kw) -> list[int]:
    return [
        await _status(mw, _scope("/api/v1/strategies/factory-templates", **kw))
        for _ in range(n)
    ]


@pytest.mark.asyncio
async def test_users_have_independent_buckets() -> None:
    """One user exhausting their quota must not touch another's."""
    mw = RateLimitMiddleware(_ok_app)
    over = _GLOBAL_QUOTA.limit + 10

    alice = await _hit(mw, over, user_id="alice")
    assert (
        alice.count(429) == 10
    ), f"alice should hit her own ceiling: {alice.count(429)}"

    # Same client IP, different identity — must still have a full budget.
    bob = await _hit(mw, over, user_id="bob")
    assert bob.count(429) == 10, f"bob's budget was spent by alice: {bob.count(429)}"


@pytest.mark.asyncio
async def test_same_ip_without_identity_shares_bucket() -> None:
    """No resolved user_id → fall back to the IP bucket (never fail open)."""
    mw = RateLimitMiddleware(_ok_app)
    over = _GLOBAL_QUOTA.limit + 10

    first = await _hit(mw, over)
    assert first.count(429) == 10
    # Anonymous traffic from the same address keeps drawing from that bucket.
    assert (await _hit(mw, 1))[0] == 429


@pytest.mark.asyncio
async def test_auth_paths_stay_ip_bucketed() -> None:
    """Credential endpoints must not shard per identity."""
    mw = RateLimitMiddleware(_ok_app)
    path = f"{_AUTH_PATH_PREFIXES[0]}/login"
    over = _AUTH_QUOTA.limit + 5

    # Different identities, same IP: all draw from one auth bucket.
    codes = [await _status(mw, _scope(path, user_id=f"u{i}")) for i in range(over)]
    assert codes.count(429) == 5, f"auth quota not shared: {codes.count(429)}"


@pytest.mark.asyncio
async def test_identity_does_not_change_auth_bucket() -> None:
    """An unauthenticated attacker gets no fresh bucket by inventing an id."""
    mw = RateLimitMiddleware(_ok_app)
    path = f"{_AUTH_PATH_PREFIXES[0]}/register"
    over = _AUTH_QUOTA.limit + 3

    anon = [await _status(mw, _scope(path)) for _ in range(over)]
    assert anon.count(429) == 3
    # Presenting an identity must not reset the auth bucket.
    assert await _status(mw, _scope(path, user_id="attacker")) == 429

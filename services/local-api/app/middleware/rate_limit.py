"""Rate limiting middleware (pure ASGI, in-memory sliding window).

Enforces request rate limits to protect against abuse.  Uses a lightweight
sliding-window counter with automatic cleanup of stale entries.

Buckets are keyed by authenticated identity where one is available (see
``_bucket_key``), because behind the deploy reverse proxy every request
shares one client IP — an IP-keyed limiter would throttle all users as a
single tenant. Auth endpoints stay IP-keyed to keep credential stuffing
expensive.

Limits are intentionally generous for normal usage but strict enough to
block automated scraping or credential-stuffing attacks.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send

# 普通请求：60 秒内最多 GLOBAL_RATE_LIMIT 次
GLOBAL_RATE_LIMIT = 120
GLOBAL_WINDOW_SECONDS = 60

# 认证端点更严：防撞库
AUTH_RATE_LIMIT = 20
AUTH_WINDOW_SECONDS = 60

# 空闲 IP 的清理周期，以及多久没活动算空闲
_CLEANUP_INTERVAL = 300
_IDLE_AFTER = 600

_AUTH_PATH_PREFIXES = ("/api/v1/auth", "/auth")
_TOO_MANY_BODY = b'{"detail":"Too many requests. Please slow down."}'


@dataclass(frozen=True)
class _Quota:
    """One bucket's limit + window."""

    limit: int
    window: float


_GLOBAL_QUOTA = _Quota(GLOBAL_RATE_LIMIT, GLOBAL_WINDOW_SECONDS)
_AUTH_QUOTA = _Quota(AUTH_RATE_LIMIT, AUTH_WINDOW_SECONDS)

#: 两张滑动窗口表的键是限流桶标识(见 _bucket_key), 值是命中时间戳(升序)。
_HitLog = dict[str, list[float]]


def _new_hit_log() -> _HitLog:
    """空滑动窗口表。"""
    return defaultdict(list)


class RateLimitMiddleware:
    """Pure ASGI middleware — tracks request counts per client IP."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # 全局额度与认证额度各用一个 ip -> 时间戳列表 的滑动窗口。
        self._requests: _HitLog = _new_hit_log()
        self._auth_requests: _HitLog = _new_hit_log()
        self._last_cleanup = time.monotonic()

    def _get_client_ip(self, scope: Scope) -> str:
        """Return the direct client IP. Does NOT trust x-forwarded-for."""
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _bucket_key(self, scope: Scope, ip: str, path: str) -> str:
        """Which quota bucket this request draws from.

        Behind a reverse proxy (the deploy topology: Caddy -> web -> api, or
        Caddy straight to api) every request arrives from ONE address, so
        keying on the client IP puts all users in a single bucket — one
        user's page load (91 requests) starves everyone else. Billing mode
        authenticates each request, and BillingGateMiddleware records the
        resolved identity as scope["state"]["user_id"], so authenticated
        traffic is metered per user instead.

        Auth endpoints are the exception and stay on the IP: they are the one
        surface an *unauthenticated* caller can reach, and their tighter quota
        exists to blunt credential stuffing — per-user bucketing would give an
        attacker a fresh bucket for every guess.

        A missing user_id falls back to the IP, so this degrades to the old
        behaviour rather than failing open.
        """
        if path.startswith(_AUTH_PATH_PREFIXES):
            return f"ip:{ip}"
        state = scope.get("state") or {}
        user_id = state.get("user_id")
        return f"user:{user_id}" if user_id else f"ip:{ip}"

    @staticmethod
    def _prune(timestamps: list[float], now: float, window: float) -> None:
        """Drop entries older than the window (list is append-ordered)."""
        cutoff = now - window
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)

    def _exceeds(
        self, store: dict[str, list[float]], key: str, now: float, quota: _Quota
    ) -> bool:
        """Record this hit and answer whether the caller is now over quota."""
        hits = store[key]
        self._prune(hits, now, quota.window)
        if len(hits) >= quota.limit:
            return True
        hits.append(now)
        return False

    @staticmethod
    def _is_idle(timestamps: list[float], now: float) -> bool:
        """窗口内无记录, 或最后一条已超出空闲阈值。"""
        return not timestamps or timestamps[-1] < now - _IDLE_AFTER

    def _cleanup_due(self, now: float) -> bool:
        """距上次清理是否已满一个周期(满则顺带记账)。"""
        if now - self._last_cleanup < _CLEANUP_INTERVAL:
            return False
        self._last_cleanup = now
        return True

    def _drop_idle_ips(self, now: float) -> None:
        """把两张表里空闲的桶摘掉。"""
        for store in (self._requests, self._auth_requests):
            stale = [key for key, hits in store.items() if self._is_idle(hits, now)]
            for key in stale:
                del store[key]

    def _maybe_cleanup(self, now: float) -> None:
        """到周期才清理: 把空闲的桶从两张表里摘掉。"""
        if self._cleanup_due(now):
            self._drop_idle_ips(now)

    def _over_quota(self, key: str, path: str, now: float) -> bool:
        """先查认证端点专属配额; 未超再查全局配额。"""
        if path.startswith(_AUTH_PATH_PREFIXES) and self._exceeds(
            self._auth_requests, key, now, _AUTH_QUOTA
        ):
            return True
        return self._exceeds(self._requests, key, now, _GLOBAL_QUOTA)

    async def _handle_http(self, scope: Scope, receive: Receive, send: Send) -> None:
        """HTTP 请求走配额检查; 超限直接回 429, 否则放行。"""
        now = time.monotonic()
        self._maybe_cleanup(now)

        path = scope.get("path", "")
        key = self._bucket_key(scope, self._get_client_ip(scope), path)
        if self._over_quota(key, path, now):
            await self._send_429(send)
            return

        await self.app(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            await self._handle_http(scope, receive, send)
        else:
            # 非 HTTP 连接(lifespan / websocket)不受限流约束。
            await self.app(scope, receive, send)

    @staticmethod
    async def _send_429(send: Send) -> None:
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/json"),
            (b"retry-after", b"60"),
            (b"content-length", str(len(_TOO_MANY_BODY)).encode()),
        ]
        await send({"type": "http.response.start", "status": 429, "headers": headers})
        await send({"type": "http.response.body", "body": _TOO_MANY_BODY})

"""Configuration models shared by the exchange SDK plumbing."""

from __future__ import annotations

import pydantic


class WSClientConfig(pydantic.BaseModel):
    """Knobs for one stream socket: where it dials and how it is policed."""

    url: str
    exchange: str
    reconnect_enabled: bool = True
    stall_timeout_s: float = pydantic.Field(30.0, gt=0)
    max_restarts_per_hour: int = pydantic.Field(10, ge=1)
    proxy_url: str | None = None


class ReconnectConfig(pydantic.BaseModel):
    """Backoff schedule used when a stream drops and must be redialled."""

    initial_delay_s: float = pydantic.Field(5.0, gt=0)
    max_delay_s: float = pydantic.Field(30.0, gt=0)
    factor: float = pydantic.Field(2.0, gt=1)
    jitter: float = pydantic.Field(0.2, ge=0, le=1)
    max_restarts_per_hour: int = pydantic.Field(10, ge=1)


class RateLimiterConfig(pydantic.BaseModel):
    """Request budget for the sliding-window limiter."""

    calls_per_window: int = pydantic.Field(1200, ge=1)
    window_ms: int = pydantic.Field(60000, ge=1000)

"""Latency monitoring types for order path observability."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LatencyStats(BaseModel):
    """Order latency percentiles across three buckets over a time window.

    Buckets:
    - intent_to_submit: time from INTENT journal write to mark_submitted
    - submit_to_ack: time from mark_submitted to mark_confirmed (exchange WS ack)
    - ack_to_fill: time from mark_confirmed to mark_filled (final trade fill)

    All values in milliseconds. Fields are nullable when no samples exist
    for the bucket in the requested window.
    """

    window_seconds: int = Field(..., description="Aggregation window length in seconds")
    sample_count: int = Field(0, description="Number of orders in the window")

    intent_to_submit_p50_ms: float | None = None
    intent_to_submit_p95_ms: float | None = None
    intent_to_submit_p99_ms: float | None = None

    submit_to_ack_p50_ms: float | None = None
    submit_to_ack_p95_ms: float | None = None
    submit_to_ack_p99_ms: float | None = None

    ack_to_fill_p50_ms: float | None = None
    ack_to_fill_p95_ms: float | None = None
    ack_to_fill_p99_ms: float | None = None

"""LLM signal schema — structured output contract for LLM-driven strategies.

Defines the pydantic models shared by ``LLMSignalStrategy`` (strategy-engine)
and ``LLMAdvisoryRule`` (risk-engine):

  - ``LLMSignal``: structured output produced by the LLM (action / confidence /
    rationale / suggested_size_pct / risk_notes).
  - ``LLMSignalConfig``: runtime configuration for the LLM signal strategy
    (provider, timeframe, fallback policy, TTL, strategy_id).

These models are deliberately provider-agnostic. The ``LLMSignal`` schema is
passed to ``LLMRouter.generate_structured`` as the ``output_schema`` so any
provider implementing structured generation can fulfill it.

Design notes (v4 final §阶段4):
  - ``LLMSignal`` is a pydantic ``BaseModel`` (not a dataclass) so it can be
    serialized to a JSON schema for structured LLM generation.
  - ``LLMSignalConfig.strategy_id`` enables KillSwitch STRATEGY scope so a
    degraded LLM provider only blocks this strategy, not A1/A2.
  - ``LLMSignalConfig.signal_ttl_s`` bounds how long a cached signal stays
    valid — stale signals are discarded by ``get_latest_signal``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# LLMSignal — structured LLM output
# ---------------------------------------------------------------------------

LLMSignalAction = Literal["long", "short", "hold"]


class LLMSignal(BaseModel):
    """Structured trading signal produced by an LLM.

    The LLM is asked to return a JSON object matching this schema. The
    strategy converts ``long``/``short`` into a ``TradeIntent`` and ignores
    ``hold`` (returns ``None`` from ``evaluate``).

    Fields:
        action: Trade direction. ``hold`` means "no trade".
        confidence: Model confidence in the action, 0.0 to 1.0.
        rationale: Human-readable reasoning (capped at 500 chars to bound
            prompt cost and UI rendering).
        suggested_size_pct: Suggested position size as a fraction of equity
            (0.0 to 1.0). Strategy clamps this to ``max_position_pct``.
        risk_notes: Optional risk caveats the LLM wants surfaced (capped at
            500 chars). Appended to the intent's ``reasoning``.
    """

    action: LLMSignalAction = Field(
        ...,
        description="Trade direction: 'long', 'short', or 'hold' (no trade).",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Model confidence in the action (0.0 to 1.0).",
    )
    rationale: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Concise reasoning for the action (max 500 chars).",
    )
    suggested_size_pct: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Suggested position size as fraction of equity (0.0 to 1.0).",
    )
    risk_notes: str = Field(
        "",
        max_length=500,
        description="Optional risk caveats to surface (max 500 chars).",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "action": "long",
                    "confidence": 0.72,
                    "rationale": "RSI oversold + bullish divergence on 4h, funding neutral.",
                    "suggested_size_pct": 0.15,
                    "risk_notes": "FOMC in 6h — reduce size if volatility expands.",
                },
                {
                    "action": "hold",
                    "confidence": 0.40,
                    "rationale": "Mixed signals: trend up but funding extreme.",
                    "suggested_size_pct": 0.0,
                    "risk_notes": "",
                },
            ]
        }
    )


# ---------------------------------------------------------------------------
# LLMSignalConfig — strategy runtime configuration
# ---------------------------------------------------------------------------


class LLMSignalConfig(BaseModel):
    """Runtime configuration for ``LLMSignalStrategy``.

    Controls LLM provider selection, market data window, fallback policy,
    signal caching TTL, and KillSwitch scoping.

    Fallback policy (v3 Gap #5 three-tier degradation):
      1. Provider fallback (router chain: primary → secondary → ...).
      2. Per-provider exponential backoff: ``max_retries`` attempts with
         delay ``retry_backoff_base_s * 2**attempt``.
      3. Exhaustion: log WARNING + skip this evaluation. If exhaustion
         happens more than ``fallback_exhausted_threshold`` times within
         ``fallback_exhausted_window_s``, the ``LLMAdvisoryRule`` raises a
         CRITICAL alert → KillSwitch STRATEGY scope activates.

    TTL (v4 Mi2): ``signal_ttl_s`` bounds cache freshness. A signal older
    than this is discarded by ``get_latest_signal``.

    Strategy scope (v4 A2): ``strategy_id`` is injected into the intent's
    ``risk_params`` so the risk engine's KillSwitch auto-trigger uses
    STRATEGY scope (only this strategy is blocked, A1/A2 keep trading).
    """

    llm_provider: str = Field(
        "deepseek",
        description="Primary LLM provider name (router will fall back to others).",
    )
    timeframe: str = Field(
        "15m",
        description="K-line timeframe fed to the prompt (e.g. '15m', '1h', '4h').",
    )
    data_points: int = Field(
        96,
        ge=10,
        le=1000,
        description="Number of recent klines to include in the prompt context.",
    )
    max_signal_history: int = Field(
        30,
        ge=0,
        description="How many past signals to retain for introspection/debugging.",
    )

    base_usdt: float = Field(
        100.0,
        gt=0,
        description="Notional base (USDT) for sizing when equity is unavailable.",
    )
    max_position_pct: float = Field(
        0.20,
        gt=0,
        le=1.0,
        description="Max fraction of equity per position (caps suggested_size_pct).",
    )

    max_retries: int = Field(
        3,
        ge=0,
        description="Per-provider retry attempts on retryable errors.",
    )
    retry_backoff_base_s: float = Field(
        1.0,
        gt=0,
        description="Base delay for exponential backoff (delay = base * 2**attempt).",
    )
    fallback_exhausted_window_s: float = Field(
        3600.0,
        gt=0,
        description="Sliding window (seconds) for counting fallback exhaustion events.",
    )
    fallback_exhausted_threshold: int = Field(
        5,
        ge=1,
        description="Exhaustion events within window before CRITICAL alert + KillSwitch.",
    )

    signal_ttl_s: float = Field(
        300.0,
        gt=0,
        description="Cached signal TTL (seconds). Older signals are discarded.",
    )

    strategy_id: str = Field(
        "llm_signal_strategy_v1",
        min_length=1,
        description="Stable ID for KillSwitch STRATEGY scope attribution.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "llm_provider": "deepseek",
                    "timeframe": "15m",
                    "data_points": 96,
                    "max_signal_history": 30,
                    "base_usdt": 100.0,
                    "max_position_pct": 0.20,
                    "max_retries": 3,
                    "retry_backoff_base_s": 1.0,
                    "fallback_exhausted_window_s": 3600,
                    "fallback_exhausted_threshold": 5,
                    "signal_ttl_s": 300.0,
                    "strategy_id": "llm_signal_strategy_v1",
                }
            ]
        }
    )

"""Shared fixtures for full-pipeline integration tests (no external services)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import pandas as pd
import pytest

from selene_space_paper.accounts import AccountStatus, PaperAccount
from selene_space_risk.engine import RiskEngine
from selene_space_risk.rules import create_default_rules

_HOUR_MS = 3_600_000
_FIRST_BAR_TS_MS = 1_704_067_200_000


def _synthetic_bars(
    n_bars: int = 800,
    seed: int = 42,
    first_price: float = 40_000.0,
    bar_interval_ms: int = _HOUR_MS,
) -> pd.DataFrame:
    """Deterministic random-walk OHLCV series for reproducible pipelines."""
    rng = np.random.RandomState(seed)

    per_bar_return = rng.normal(loc=0.0002, scale=0.015, size=n_bars)
    close = np.empty(n_bars)
    close[0] = first_price
    for i in range(1, n_bars):
        close[i] = close[i - 1] * np.exp(per_bar_return[i])

    open_ = np.roll(close, 1)
    open_[0] = first_price * 0.999

    up_wick = rng.uniform(0.002, 0.010, size=n_bars)
    down_wick = rng.uniform(0.002, 0.010, size=n_bars)
    high = np.maximum(open_, close) * (1 + up_wick)
    low = np.minimum(open_, close) * (1 - down_wick)
    volume = rng.uniform(50.0, 500.0, size=n_bars)

    ts = _FIRST_BAR_TS_MS + np.arange(n_bars) * bar_interval_ms

    return pd.DataFrame(
        {
            "closed": True,
            "volume": np.round(volume, 2),
            "low": np.round(low, 2),
            "close": np.round(close, 2),
            "high": np.round(high, 2),
            "open": np.round(open_, 2),
            "interval": "1h",
            "symbol": "BTC/USDT",
            "exchange": "backtest",
            "timestamp": ts.astype(np.int64),
        }
    )


@pytest.fixture
def demo_data() -> pd.DataFrame:
    """800 synthetic BTC/USDT 1h bars; nothing read from the repo's demo/ dir."""
    return _synthetic_bars(n_bars=800, seed=42)


class _ScriptedLLM:
    """Replies from a fixed script; satisfies AgentRuntime's LLM surface."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script = list(script)
        self._cursor = 0

    def _next(self) -> dict[str, Any]:
        if self._cursor >= len(self._script):
            return {"response": "done", "tool_calls": []}
        entry = self._script[self._cursor]
        self._cursor += 1
        return entry

    async def chat(self, messages: list[Any], **kwargs: Any) -> str:
        import json

        return json.dumps(self._next())

    async def chat_stream(self, messages: list[Any], **kwargs: Any) -> AsyncIterator[str]:
        async def _silent() -> AsyncIterator[str]:
            if False:
                yield ""

        return _silent()

    async def generate_structured(
        self, messages: list[Any], output_schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        return self._next()

    async def chat_with_tools(
        self, messages: list[Any], tools: list[dict[str, Any]] | None = None, **kwargs: Any
    ) -> Any:
        from selene_space_llm.schemas import TokenUsage, ToolCall, ToolCallResult

        entry = self._next()
        if entry.get("response") == "done" and not entry.get("tool_calls"):
            return ToolCallResult(text="done")
        wanted = [
            tc for tc in entry.get("tool_calls", []) if isinstance(tc, dict)
        ]
        calls = [
            ToolCall(id=f"int_call_{i}", name=tc.get("tool", ""), arguments=tc.get("arguments", {}))
            for i, tc in enumerate(wanted)
        ]
        return ToolCallResult(
            usage=TokenUsage(),
            # ``ToolCallResult.text`` is ``str`` (not Optional): passing None
            # raises a validation error that the runtime swallows into a generic
            # "LLM error" — the tool loop then silently never runs. Production
            # providers coerce with ``str(x or "")`` for the same reason.
            text=entry.get("response", "") or "",
            tool_calls=calls,
        )


@pytest.fixture
def mock_llm() -> _ScriptedLLM:
    """LLM scripted to issue one safe echo tool call and then finish."""
    return _ScriptedLLM(
        [
            {
                "tool_calls": [{"arguments": {"msg": "ping"}, "tool": "integration_echo"}],
                "response": "",
            },
        ]
    )


@pytest.fixture
def paper_account() -> PaperAccount:
    """Paper account seeded with a $10,000 balance."""
    now_ms = int(time.time() * 1000)
    return PaperAccount(
        updated_at=now_ms,
        created_at=now_ms,
        status=AccountStatus.ACTIVE,
        current_balance=10_000.0,
        initial_balance=10_000.0,
        name="integration-test",
    )


@pytest.fixture
def risk_engine() -> RiskEngine:
    """Risk engine loaded with the default rule pack."""
    return RiskEngine(create_default_rules())

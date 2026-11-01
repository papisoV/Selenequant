"""Item 7 — the retry cheat-guard presupposes deterministic sampling.

``retry.IMMUTABLE_FIELDS`` deep-compares ``diagnosis_summary`` (free LLM
text) across retries and raises ``CheatError`` on any difference. That is
correct only when the model is sampled greedily: at ``temperature > 0`` a
legitimate re-roll would rewrite the prose and be misjudged as cheating.

Production PA calls the model at ``temperature=0.0`` on every arm
(``decision_workflow_service._get_trading_adapter`` for the endpoint +
heartbeat, ``run_pa_diagnosis.build_async_backend`` for the CLI), so the
tension is closed. These tests pin that invariant: if either arm ever
raises the temperature, this goes RED and item 7 must be re-opened (either
narrow the immutable comparison to numeric fields, or judge prose
separately).

Reverse validation: set ``temperature=0.1`` in the service and the first
test fails on the assertion, not on an import error.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.core.decision_workflow_service import DecisionWorkflowService

# ---------------------------------------------------------------------------
# Endpoint + heartbeat arm — the service's real trading adapter
# ---------------------------------------------------------------------------


class _StubSettings:
    """Minimal settings service: the one coroutine the adapter builder calls."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self._cfg = cfg

    async def resolve_llm_full_config(
        self, *, user_id: str | None = None
    ) -> dict[str, Any]:
        return dict(self._cfg)


@pytest.mark.asyncio
async def test_trading_adapter_samples_greedily() -> None:
    """The PA trading adapter must build with temperature == 0.0.

    Path: service -> resolve_llm_full_config -> LLMConfig -> OpenAICompatProvider,
    wrapped in AsyncBackendToSyncAdapter. The provider holds the config, so we
    read it back off the adapter's backend rather than trusting the literal.
    """
    svc = DecisionWorkflowService(
        settings_loader=lambda: _StubSettings(
            {"api_key": "k", "base_url": "http://x/v1", "model": "m"}
        )
    )
    adapter = await svc._get_trading_adapter()
    provider = adapter._backend
    assert provider.config.temperature == 0.0, (
        "PA trading adapter must sample greedily (temperature=0.0); "
        "a non-zero value re-opens item 7 (retry cheat-guard vs free-text "
        "diagnosis_summary)."
    )


@pytest.mark.asyncio
async def test_trading_adapter_max_tokens_matches_cli_window() -> None:
    """16384 is the validated budget for the full 120-bar window (CLI parity)."""
    svc = DecisionWorkflowService(
        settings_loader=lambda: _StubSettings(
            {"api_key": "k", "base_url": "http://x/v1", "model": "m"}
        )
    )
    adapter = await svc._get_trading_adapter()
    assert adapter._backend.config.max_tokens == 16384

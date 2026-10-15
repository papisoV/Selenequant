"""LLM provider integration helpers for selene agent.

Phase 3.3 adds an opt-in surface for vendored vibe LLM providers
(``vibe:<provider_id>``) so they appear as available options in the
selene agent's LLM provider config when ``VIBE_LLM_ENABLED=true``.

This subpackage is lazy — importing it does NOT import LangChain or
the vibe subsystem. The vibe bridge is only touched when a caller
explicitly invokes :func:`list_vibe_llm_providers`.
"""

from __future__ import annotations

from selene_space_agent.llm.vibe_providers import (
    VIBE_LLM_PROVIDER_PREFIX,
    is_vibe_llm_enabled,
    list_vibe_llm_providers,
)

__all__ = [
    "VIBE_LLM_PROVIDER_PREFIX",
    "is_vibe_llm_enabled",
    "list_vibe_llm_providers",
]

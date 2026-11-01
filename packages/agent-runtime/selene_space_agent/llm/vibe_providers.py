"""Opt-in surface for vendored vibe LLM providers.

When ``VIBE_LLM_ENABLED=true``, selene can list vibe's LLM providers as
namespaced options (``vibe:<id>``). The vibe bridge is imported lazily
inside :func:`list_vibe_llm_providers` so module load never touches
LangChain, and any bridge failure degrades to an empty list.
"""

from __future__ import annotations

import logging
import os

VIBE_LLM_ENABLED_ENV = "VIBE_LLM_ENABLED"
VIBE_LLM_PROVIDER_PREFIX = "vibe:"

_VIBE_LLM_TRUTHY = frozenset({"true", "1", "yes"})

logger = logging.getLogger(__name__)


def is_vibe_llm_enabled() -> bool:
    """Check the ``VIBE_LLM_ENABLED`` env var (case-insensitive truthy set)."""
    raw = os.environ.get(VIBE_LLM_ENABLED_ENV, "")
    return raw.strip().lower() in _VIBE_LLM_TRUTHY


def list_vibe_llm_providers() -> list[str]:
    """Return sorted ``vibe:<id>`` provider IDs; [] when disabled/unavailable."""
    if not is_vibe_llm_enabled():
        return []

    try:
        from subsystems.vibe.bridge.llm.preset import list_vibe_providers
    except Exception:
        logger.warning("vibe LLM bridge unavailable", exc_info=True)
        return []

    try:
        raw_ids = list_vibe_providers()
    except Exception:
        logger.warning("vibe LLM provider listing failed", exc_info=True)
        return []

    namespaced: set[str] = set()
    for provider_id in raw_ids or []:
        if not isinstance(provider_id, str) or not provider_id:
            continue
        if provider_id.startswith(VIBE_LLM_PROVIDER_PREFIX):
            continue
        namespaced.add(f"{VIBE_LLM_PROVIDER_PREFIX}{provider_id}")
    return sorted(namespaced)

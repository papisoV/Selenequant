"""Registry for selene_space_agentstudio providers.

Mirrors selene_space_optimize.registry — register_provider / get_provider /
list_registered. Providers auto-register on import via the `register()`
pattern called at the bottom of each provider module.
"""

from __future__ import annotations

from typing import Callable

from selene_space_agentstudio.base import (
    AgentStudioProvider,
    AgentStudioProviderConfig,
)

_REGISTRY: dict[str, Callable[[AgentStudioProviderConfig], AgentStudioProvider]] = {}


def register_provider(
    name: str,
    factory: Callable[[AgentStudioProviderConfig], AgentStudioProvider],
) -> None:
    """Register a provider factory under `name`."""
    _REGISTRY[name] = factory


def get_provider(cfg: AgentStudioProviderConfig) -> AgentStudioProvider:
    """Instantiate a registered provider by cfg.provider id.

    Raises KeyError if not registered.
    """
    if cfg.provider not in _REGISTRY:
        raise KeyError(
            f"Unknown agent studio provider: {cfg.provider}. "
            f"Registered: {list_registered()}"
        )
    return _REGISTRY[cfg.provider](cfg)


def list_registered() -> list[str]:
    """List registered provider ids (sorted)."""
    return sorted(_REGISTRY.keys())

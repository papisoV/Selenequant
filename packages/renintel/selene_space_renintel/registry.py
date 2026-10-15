"""Registry for selene_space_renintel providers.

Mirrors selene_space_agentstudio.registry.
"""

from __future__ import annotations

from typing import Callable

from selene_space_renintel.base import (
    RenIntelProvider,
    RenIntelProviderConfig,
)

_REGISTRY: dict[str, Callable[[RenIntelProviderConfig], RenIntelProvider]] = {}


def register_provider(
    name: str,
    factory: Callable[[RenIntelProviderConfig], RenIntelProvider],
) -> None:
    """Register a provider factory under `name`."""
    _REGISTRY[name] = factory


def get_provider(cfg: RenIntelProviderConfig) -> RenIntelProvider:
    """Instantiate a registered provider by cfg.provider id.

    Raises KeyError if not registered.
    """
    if cfg.provider not in _REGISTRY:
        raise KeyError(
            f"Unknown renintel provider: {cfg.provider}. "
            f"Registered: {list_registered()}"
        )
    return _REGISTRY[cfg.provider](cfg)


def list_registered() -> list[str]:
    """List registered provider ids (sorted)."""
    return sorted(_REGISTRY.keys())

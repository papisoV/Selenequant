"""Registry for optimize providers — same pattern as analytics/macrodata."""

from __future__ import annotations

from typing import Callable

from selene_space_optimize.base import OptimizeProvider, OptimizeProviderConfig

RegistryFactory = Callable[[OptimizeProviderConfig], OptimizeProvider]

_PROVIDERS: dict[str, RegistryFactory] = {}


def register_provider(name: str, factory: RegistryFactory) -> None:
    """Register an optimize provider factory under `name`."""
    if name in _PROVIDERS:
        return
    _PROVIDERS[name] = factory


def get_provider(cfg: OptimizeProviderConfig) -> OptimizeProvider:
    """Instantiate a registered provider by id. Raises KeyError if unknown."""
    try:
        factory = _PROVIDERS[cfg.provider]
    except KeyError as exc:
        raise KeyError(f"Unknown optimize provider: {cfg.provider}") from exc
    return factory(cfg)


def list_registered() -> list[str]:
    """Return sorted list of registered provider ids."""
    return sorted(_PROVIDERS.keys())

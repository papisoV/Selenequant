"""Provider registry — pluggable provider lookup by id."""

from __future__ import annotations

from typing import Callable

from selene_space_macrodata.source import MacroDataSource, MacroDataSourceConfig

ProviderFactory = Callable[[MacroDataSourceConfig], MacroDataSource]

_REGISTRY: dict[str, ProviderFactory] = {}


def register_provider(provider_id: str, factory: ProviderFactory) -> None:
    """Register a provider factory under provider_id (e.g. 'fred')."""
    _REGISTRY[provider_id] = factory


def get_provider(cfg: MacroDataSourceConfig) -> MacroDataSource:
    """Look up and instantiate a provider by cfg.provider id."""
    if cfg.provider not in _REGISTRY:
        raise KeyError(
            f"Unknown macro data provider: {cfg.provider}. "
            f"Registered: {list(_REGISTRY)}"
        )
    return _REGISTRY[cfg.provider](cfg)


def list_registered() -> list[str]:
    """Return all registered provider ids."""
    return list(_REGISTRY)

"""selene_space_renintel — Renaissance Technologies hedge fund agent framework.

Provides Renaissance-style hedge fund agent framework: 12 agent personas,
4 reasoning patterns, 4 guardrails, 6 strategies, 5 memory types.

All data is static (clean-room reimplementation, no source code copied).
"""

from selene_space_renintel.base import (
    RenIntelProvider,
    RenIntelProviderConfig,
    RenIntelReport,
    RenIntelKind,
)
from selene_space_renintel.registry import (
    get_provider,
    list_registered,
    register_provider,
)
from selene_space_renintel import providers  # noqa: F401  — auto-register

__all__ = [
    "RenIntelProvider",
    "RenIntelProviderConfig",
    "RenIntelReport",
    "RenIntelKind",
    "get_provider",
    "list_registered",
    "register_provider",
    "providers",
]

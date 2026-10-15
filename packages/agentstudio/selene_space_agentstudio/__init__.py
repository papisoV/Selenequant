"""selene_space_agentstudio — Multi-agent R&D loops + domain prompt library.

Provides 7 R&D loop providers (factor / model / quant / report / kaggle / ds /
multitrace) inspired by the rdagents framework and commercial quant terminals
(clean-room reimplementation, no source code copied). Aligned with batch1-3
(selene_space_macrodata / selene_space_alpha.analytics / selene_space_optimize)
patterns. Heavy deps (rdagents, litellm) are lazy-imported for graceful
degradation.

Two subpackages:
- providers/: 7 R&D loop providers (factor / model / quant / report / kaggle / ds / multitrace)
- prompts/: 4 domain agent prompt packs (Economic / Geopolitics / TraderInvestor / HedgeFund-Renaissance)
"""

from selene_space_agentstudio.base import (
    AgentStudioProvider,
    AgentStudioProviderConfig,
    AgentStudioReport,
    AgentStudioKind,
)
from selene_space_agentstudio.registry import (
    get_provider,
    list_registered,
    register_provider,
)
from selene_space_agentstudio import providers  # noqa: F401  — auto-register

__all__ = [
    "AgentStudioProvider",
    "AgentStudioProviderConfig",
    "AgentStudioReport",
    "AgentStudioKind",
    "get_provider",
    "list_registered",
    "register_provider",
    "providers",
]

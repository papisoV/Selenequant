"""selene_space_renintel providers — 5 providers covering Renaissance framework.

Providers:
- personas: 12 agent personas (quant_researcher / signal_scientist / ...)
- reasoning: 4 reasoning patterns (signal_analysis / risk / ic_deliberation / investment)
- guardrails: 4 guardrails (compliance / execution / risk / signal)
- strategies: 6 quant strategies (mean_reversion / momentum / stat_arb / ...)
- memory: 5 memory types (agent / decision / learning / trade / seed)

All providers use static fallback data (clean-room reimplementation, no source code copied).
"""

from selene_space_renintel.providers.persona_provider import PersonaProvider
from selene_space_renintel.providers.reasoning_provider import ReasoningProvider
from selene_space_renintel.providers.guardrail_provider import GuardrailProvider
from selene_space_renintel.providers.strategy_provider import StrategyProvider
from selene_space_renintel.providers.memory_provider import MemoryProvider
from selene_space_renintel.registry import register_provider

# Register all 5 providers
register_provider("personas", lambda cfg: PersonaProvider(cfg))
register_provider("reasoning", lambda cfg: ReasoningProvider(cfg))
register_provider("guardrails", lambda cfg: GuardrailProvider(cfg))
register_provider("strategies", lambda cfg: StrategyProvider(cfg))
register_provider("memory", lambda cfg: MemoryProvider(cfg))

__all__ = [
    "PersonaProvider",
    "ReasoningProvider",
    "GuardrailProvider",
    "StrategyProvider",
    "MemoryProvider",
]

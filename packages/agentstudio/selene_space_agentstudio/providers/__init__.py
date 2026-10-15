"""Agent Studio providers — 7 R&D loops + 1 prompt library.

R&D loop providers wrap the rdagents framework's 7 loops (factor / model /
quant / report / kaggle / ds / multitrace), inspired by commercial quant
terminal patterns (clean-room reimplementation, no source code copied). Loops
are lazy-imported with try/except so the package imports cleanly even when
rdagents is not installed.

Prompt library provider exposes 4 domain agent prompt packs
(Economic / Geopolitics / TraderInvestor / HedgeFund-Renaissance) as
data artifacts for the API/UI.
"""

from selene_space_agentstudio.providers.rd_loop_provider import register_rd_loops
from selene_space_agentstudio.providers.prompt_library_provider import (
    PromptLibraryProvider,
)

# Register all 7 R&D loops
register_rd_loops()

# Register prompt library provider
from selene_space_agentstudio.registry import register_provider  # noqa: E402

register_provider(
    "prompt_library",
    lambda cfg: PromptLibraryProvider(cfg),
)

__all__ = ["register_rd_loops", "PromptLibraryProvider"]

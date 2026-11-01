"""selene_space_optimize — RL trading agents + portfolio optimization.

Two subpackages:
- rl/: SB3-based RL agents (PPO, DQN, A2C, SAC, TD3) with gymnasium envs
- portfolio/: scipy-based optimization (risk_parity, mean_variance, enhanced_index)

Design aligned with selene_space_macrodata / selene_space_alpha.analytics
(Protocol + Registry pattern). Heavy RL deps (stable-baselines3, gymnasium)
are lazy-imported so the package can be imported even when RL extras are not
installed (graceful degradation).
"""

from selene_space_optimize.base import (
    OptimizeProvider,
    OptimizeProviderConfig,
    OptimizeReport,
    OptimizeKind,
)
from selene_space_optimize.registry import (
    get_provider,
    list_registered,
    register_provider,
)
from selene_space_optimize import providers  # noqa: F401  — auto-register

__all__ = [
    "OptimizeProvider",
    "OptimizeProviderConfig",
    "OptimizeReport",
    "OptimizeKind",
    "get_provider",
    "list_registered",
    "register_provider",
    "providers",
]

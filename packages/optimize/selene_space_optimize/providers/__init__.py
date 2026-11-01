"""Optimize providers package — auto-registers all providers on import.

Portfolio providers (scipy only) always register. RL providers (PPO/DQN/A2C/
SAC/TD3) register unconditionally — healthcheck() returns False and compute()
returns a friendly error if stable-baselines3 is not installed.
"""

# Portfolio providers — scipy only, always available
from selene_space_optimize.providers import (  # noqa: F401
    risk_parity_provider,
    mean_variance_provider,
    enhanced_index_provider,
)

# RL providers — registered unconditionally; SB3 lazy-imported on use
from selene_space_optimize.providers import rl_provider  # noqa: F401

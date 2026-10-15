"""Shared RL utilities — gymnasium trading env + SB3 model factory.

Lazy-imports stable_baselines3 + gymnasium so the package imports cleanly
even when RL extras are not installed.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Algorithm registry: name -> (sb3_class_name, discrete_action)
RL_ALGORITHMS: dict[str, tuple[str, bool]] = {
    "ppo": ("PPO", True),
    "dqn": ("DQN", True),
    "a2c": ("A2C", True),
    "sac": ("SAC", False),
    "td3": ("TD3", False),
}


def sb3_available() -> bool:
    """Check if stable-baselines3 + gymnasium are importable."""
    try:
        import stable_baselines3  # noqa: F401, PLC0415
        import gymnasium  # noqa: F401, PLC0415
        return True
    except ImportError:
        return False


def get_sb3_class(name: str) -> type:
    """Get SB3 algorithm class by name (PPO/DQN/A2C/SAC/TD3)."""
    import stable_baselines3 as sb3  # noqa: PLC0415
    return getattr(sb3, name)


class TradingEnv:
    """Minimal gymnasium-compatible trading env (single-asset, long/flat).

    Observation: [normalized_returns_window, position_one_hot]
    Action: 0=flat, 1=long (discrete) OR [target_weight] (continuous)
    Reward: step return * position - transaction_cost * |Δposition|

    Inherits from gymnasium.Env at instance creation (lazy import) so the
    package can be imported even when gymnasium is not installed.
    """

    def __init__(self, prices: pd.Series, window: int = 20, cost_bps: float = 1.0) -> None:
        import gymnasium as gym  # noqa: PLC0415
        # Dynamically inherit from gym.Env at instantiation time
        if not isinstance(self, gym.Env):
            self.__class__ = type(self.__class__.__name__, (self.__class__, gym.Env), {})
        self._prices = prices.to_numpy().astype(float)
        self._window = int(window)
        self._cost = float(cost_bps) / 10000.0
        self._n = len(self._prices)
        self._t = 0
        self._position = 0.0  # 0=flat, 1=long
        obs_size = self._window + 1
        self._obs_size = obs_size
        self.observation_space = gym.spaces.Box(
            low=-10.0, high=10.0, shape=(obs_size,), dtype=np.float32,
        )
        # Discrete: 2 actions (flat, long). Continuous: [0, 1] target weight.
        self.action_space = gym.spaces.Discrete(2)

    def reset(self, *, seed: int | None = None, options: dict | None = None) -> tuple[np.ndarray, dict]:
        super().__init__() if False else None  # no-op (avoid gymnasium inheritance)
        self._t = self._window
        self._position = 0.0
        return self._obs(), {}

    def _obs(self) -> np.ndarray:
        """Window of past returns + current position."""
        start = max(0, self._t - self._window)
        rets = np.diff(self._prices[start:self._t]) / (self._prices[start:self._t - 1] + 1e-9)
        padded = np.zeros(self._window - len(rets) + 1, dtype=np.float32)
        rets_norm = np.concatenate([padded, rets.astype(np.float32)])[-self._window:]
        return np.concatenate([rets_norm, [self._position]]).astype(np.float32)

    def step(self, action: int | np.ndarray) -> tuple[np.ndarray, float, bool, bool, dict]:
        if self._t >= self._n - 1:
            return self._obs(), 0.0, True, False, {}
        if isinstance(action, (np.ndarray, list)):
            target = float(np.clip(action[0], 0.0, 1.0))
        else:
            target = 1.0 if int(action) == 1 else 0.0
        prev_price = self._prices[self._t]
        next_price = self._prices[self._t + 1]
        ret = (next_price - prev_price) / (prev_price + 1e-9)
        cost = abs(target - self._position) * self._cost
        reward = self._position * ret - cost
        self._position = target
        self._t += 1
        done = self._t >= self._n - 1
        return self._obs(), float(reward), done, False, {"position": self._position, "ret": ret}

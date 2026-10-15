"""RL algorithms registry — PPO/DQN/A2C/SAC/TD3 share a common provider.

Each algorithm is registered as a separate provider id so the API can route
requests to the right SB3 class. All use the same RLProvider class with a
different `algo_name` injected via config.params.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from selene_space_optimize.base import (
    OptimizeKind,
    OptimizeProviderConfig,
    OptimizeReport,
)
from selene_space_optimize.rl.base import RL_ALGORITHMS, TradingEnv, sb3_available

logger = logging.getLogger(__name__)


class RLProvider:
    """Async RL provider — one of PPO/DQN/A2C/SAC/TD3 based on cfg.provider."""

    def __init__(self, cfg: OptimizeProviderConfig) -> None:
        if cfg.provider not in RL_ALGORITHMS:
            raise KeyError(f"Unknown RL algorithm: {cfg.provider}")
        self._cfg = cfg
        self._algo_name, self._discrete = RL_ALGORITHMS[cfg.provider]
        self._running = False
        self._model: Any = None

    @property
    def config(self) -> OptimizeProviderConfig:
        return self._cfg

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        self._model = None

    async def healthcheck(self) -> bool:
        return sb3_available()

    async def compute(
        self,
        *,
        action: str,
        prices: pd.DataFrame | pd.Series | None = None,
        returns: pd.DataFrame | pd.Series | None = None,
        weights: list[float] | None = None,
        benchmark: pd.DataFrame | pd.Series | None = None,
        params: dict[str, Any] | None = None,
    ) -> OptimizeReport:
        if action not in {"train", "predict", "info"}:
            raise KeyError(f"Unknown action: {action}")
        if action == "info":
            return OptimizeReport(
                provider=self._cfg.provider,
                kind=OptimizeKind.OTHER,
                title=f"{self._algo_name} Info",
                metrics={
                    "algorithm": self._algo_name,
                    "discrete_action": self._discrete,
                    "sb3_available": sb3_available(),
                },
                computed_at=datetime.now(UTC),
            )

        if not sb3_available():
            return OptimizeReport(
                provider=self._cfg.provider,
                kind=OptimizeKind.RL_TRAIN if action == "train" else OptimizeKind.RL_PREDICT,
                title=f"{self._algo_name} {action} (unavailable)",
                metrics={
                    "error": "stable-baselines3 / gymnasium not installed",
                    "hint": "pip install -e packages/optimize[rl]",
                },
                computed_at=datetime.now(UTC),
                notes="RL extras not installed",
            )

        if prices is None and returns is None:
            raise ValueError(f"{self._cfg.provider}.{action} requires prices or returns")
        series = prices if prices is not None else returns
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        series = series.dropna()
        if len(series) < 50:
            raise ValueError("series too short (<50 points) for RL training")

        params = params or {}
        if action == "train":
            return self._train(series, params)
        return self._predict(series, params)

    def _train(self, series: pd.Series, params: dict[str, Any]) -> OptimizeReport:
        from stable_baselines3.common.vec_env import DummyVecEnv  # noqa: PLC0415

        window = int(params.get("window", 20))
        cost_bps = float(params.get("cost_bps", 1.0))
        total_timesteps = int(params.get("total_timesteps", 5000))
        policy = params.get("policy", "MlpPolicy")

        def make_env() -> TradingEnv:
            return TradingEnv(series, window=window, cost_bps=cost_bps)

        vec_env = DummyVecEnv([make_env])
        try:
            cls = self._get_sb3_class()
            model = cls(policy, vec_env, verbose=0, seed=42)
            model.learn(total_timesteps=total_timesteps)
            self._model = model

            # Evaluate on training data for smoke metric
            obs = vec_env.reset()
            total_reward = 0.0
            n_steps = 0
            done = False
            while not done:
                act, _ = model.predict(obs, deterministic=True)
                obs, reward, done_arr, _ = vec_env.step(act)
                total_reward += float(reward[0])
                n_steps += 1
                done = bool(done_arr[0])

            metrics = {
                "total_timesteps": total_timesteps,
                "train_reward": round(total_reward, 6),
                "train_steps": n_steps,
                "algorithm": self._algo_name,
            }
            notes = f"Trained {self._algo_name} on {len(series)} timesteps"
            return OptimizeReport(
                provider=self._cfg.provider,
                kind=OptimizeKind.RL_TRAIN,
                title=f"{self._algo_name} Training",
                metrics=metrics,
                computed_at=datetime.now(UTC),
                notes=notes,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s train failed: %s", self._algo_name, exc)
            return OptimizeReport(
                provider=self._cfg.provider,
                kind=OptimizeKind.RL_TRAIN,
                title=f"{self._algo_name} Training (failed)",
                metrics={"error": str(exc)},
                computed_at=datetime.now(UTC),
                notes="Training failed",
            )

    def _predict(self, series: pd.Series, params: dict[str, Any]) -> OptimizeReport:
        if self._model is None:
            return OptimizeReport(
                provider=self._cfg.provider,
                kind=OptimizeKind.RL_PREDICT,
                title=f"{self._algo_name} Predict (no model)",
                metrics={"error": "no trained model — call train first"},
                computed_at=datetime.now(UTC),
                notes="Model not trained",
            )
        window = int(params.get("window", 20))
        cost_bps = float(params.get("cost_bps", 1.0))
        env = TradingEnv(series, window=window, cost_bps=cost_bps)
        try:
            obs, _ = env.reset()
            actions: list[int] = []
            rewards: list[float] = []
            done = False
            while not done:
                act, _ = self._model.predict(obs, deterministic=True)
                # SB3 may return scalar, 0-d, or 1-d array — normalize to int
                if isinstance(act, (list, np.ndarray)):
                    act_int = int(np.asarray(act).reshape(-1)[0])
                else:
                    act_int = int(act)
                obs, reward, done, _, _ = env.step(act_int)
                actions.append(act_int)
                rewards.append(reward)
            metrics = {
                "total_reward": round(float(sum(rewards)), 6),
                "n_steps": len(actions),
                "long_pct": round(sum(1 for a in actions if a == 1) / max(1, len(actions)), 6),
                "algorithm": self._algo_name,
            }
            data = pd.DataFrame(
                {"action": actions, "reward": rewards},
                index=series.index[-len(actions):],
            )
            return OptimizeReport(
                provider=self._cfg.provider,
                kind=OptimizeKind.RL_PREDICT,
                title=f"{self._algo_name} Prediction",
                metrics=metrics,
                data=data,
                computed_at=datetime.now(UTC),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s predict failed: %s", self._algo_name, exc)
            return OptimizeReport(
                provider=self._cfg.provider,
                kind=OptimizeKind.RL_PREDICT,
                title=f"{self._algo_name} Prediction (failed)",
                metrics={"error": str(exc)},
                computed_at=datetime.now(UTC),
            )

    def _get_sb3_class(self) -> type:
        import stable_baselines3 as sb3  # noqa: PLC0415
        return getattr(sb3, self._algo_name)

    def list_actions(self) -> list[str]:
        return ["train", "predict", "info"]

    def describe_action(self, action: str) -> str:
        return {
            "train": f"Train {self._algo_name} agent (params: total_timesteps, window, cost_bps, policy)",
            "predict": f"Run trained {self._algo_name} agent on data",
            "info": "Provider info",
        }.get(action, action)


def register_rl_provider(algo: str) -> None:
    """Register one RL algorithm (ppo/dqn/a2c/sac/td3) with a closure."""
    from selene_space_optimize.registry import register_provider

    def factory(cfg: OptimizeProviderConfig) -> RLProvider:
        # Force cfg.provider to the registered algo so the closure captures it.
        cfg_with_algo = cfg.model_copy(update={"provider": algo})
        return RLProvider(cfg_with_algo)

    register_provider(algo, factory)


# Register all 5 algorithms
for _algo in ("ppo", "dqn", "a2c", "sac", "td3"):
    register_rl_provider(_algo)

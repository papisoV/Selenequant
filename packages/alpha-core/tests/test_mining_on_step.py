"""Engine on_step callback contract (P0 training visualization).

Locks the optional ``on_step`` hook added to ``AlphaEngine.train``:
- invoked once per step with ``(step, avg_reward, best_score, stable_rank, log_line)``
- ``stable_rank`` is ``None`` unless LoRD active AND ``step % 100 == 0``
- ``log_line`` is ``None`` while no best formula found yet
- default ``None`` → zero behavior change (loop still completes, history still saved)
"""

from __future__ import annotations

import torch

from selene_space_alpha.config import AlphaCoreConfig
from selene_space_alpha.mining.engine import AlphaEngine


class _TinyConfig(AlphaCoreConfig):
    DEVICE = torch.device("cpu")
    BATCH_SIZE = 8
    TRAIN_STEPS = 5
    MAX_FORMULA_LEN = 6


class _FakeLoader:
    """Minimal loader: a constant feature tensor + raw data cache + target ret."""

    def __init__(self) -> None:
        self.feat_tensor = torch.randn(3, 4, 50)
        # Action masking makes sampled formulas valid, so the loop really
        # reaches MemeBacktest.evaluate — liquidity must be present.
        self.raw_data_cache: dict[str, object] = {"liquidity": torch.full((3, 50), 1e6)}
        self.target_ret = torch.randn(50)
        self.loaded = False

    def load_data(self) -> None:
        self.loaded = True


def _engine(tmp_path, *, use_lord: bool = True) -> AlphaEngine:
    return AlphaEngine(
        data_loader=_FakeLoader(),
        config=_TinyConfig(),
        use_lord_regularization=use_lord,
        output_dir=str(tmp_path),
    )


def test_on_step_invoked_per_step(tmp_path) -> None:
    torch.manual_seed(0)
    engine = _engine(tmp_path, use_lord=False)
    calls: list[tuple] = []
    engine.train(on_step=lambda *a: calls.append(a))

    assert len(calls) == _TinyConfig.TRAIN_STEPS
    for i, (step, avg_reward, best_score, stable_rank, log_line) in enumerate(calls):
        assert step == i
        assert isinstance(avg_reward, float)
        assert isinstance(best_score, float)
        # LoRD off → stable_rank always None
        assert stable_rank is None
    # log_line carries the best-formula line once one exists; with action
    # masking a valid formula lands on step 0, so it is non-None throughout.
    assert calls[0][4] is not None


def test_on_step_stable_rank_lord(tmp_path) -> None:
    torch.manual_seed(0)
    engine = _engine(tmp_path, use_lord=True)
    calls: list[tuple] = []
    engine.train(on_step=lambda *a: calls.append(a))

    assert len(calls) == _TinyConfig.TRAIN_STEPS
    # LoRD computes stable_rank whenever step % 100 == 0 → only step 0 in a 5-step run.
    assert calls[0][3] is not None
    for i in range(1, _TinyConfig.TRAIN_STEPS):
        assert calls[i][3] is None


def test_on_step_default_none_no_behavior_change(tmp_path) -> None:
    torch.manual_seed(0)
    engine = _engine(tmp_path, use_lord=False)
    engine.train()  # must not raise
    assert len(engine.training_history["step"]) == _TinyConfig.TRAIN_STEPS
    assert (tmp_path / "training_history.json").exists()

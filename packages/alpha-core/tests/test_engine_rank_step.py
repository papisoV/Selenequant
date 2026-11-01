"""US-001/US-003: rank_step + reward_std history contract.

``stable_rank`` is sparse (only LoRD runs, only every 100th step) while
``step``/``avg_reward``/``best_score`` append every step. ``rank_step``
appends in the same sparse branch so ``zip(rank_step, stable_rank)`` is the
[(step, rank)] alignment contract — the frontend stops depending on the
step%100 convention. ``reward_std`` (per-step batch rewards std) appends
every step and is the mechanical basis for the advantage band.

Locked here:
- rank_step appends in lockstep with stable_rank (same branch, same step)
- rank_step/stable_rank stay empty together when LoRD is off
- reward_std appends once per step, non-negative
- checkpoint round-trip preserves both keys' lengths
- a checkpoint written before these keys existed still loads (merge, not
  replace) and a resumed run appends without KeyError
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
    def __init__(self) -> None:
        self.feat_tensor = torch.randn(3, 4, 50)
        # Action masking makes sampled formulas valid, so the loop really
        # reaches MemeBacktest.evaluate — liquidity must be present.
        self.raw_data_cache: dict[str, object] = {"liquidity": torch.full((3, 50), 1e6)}
        self.target_ret = torch.randn(50)

    def load_data(self) -> None:
        pass


def _engine(tmp_path, *, use_lord: bool = False) -> AlphaEngine:
    return AlphaEngine(
        data_loader=_FakeLoader(),
        config=_TinyConfig(),
        use_lord_regularization=use_lord,
        output_dir=str(tmp_path),
    )


def test_rank_step_appends_in_lockstep_with_stable_rank(tmp_path) -> None:
    """LoRD on: every stable_rank sample is paired with its step."""
    torch.manual_seed(0)
    engine = _engine(tmp_path, use_lord=True)
    engine.train()

    steps = engine.training_history["step"]
    ranks = engine.training_history["stable_rank"]
    rank_steps = engine.training_history["rank_step"]

    assert len(rank_steps) == len(ranks)
    assert rank_steps == [s for s in steps if s % 100 == 0]
    assert list(zip(rank_steps, ranks)) == [(0, ranks[0])]


def test_rank_step_stays_empty_when_lord_off(tmp_path) -> None:
    torch.manual_seed(0)
    engine = _engine(tmp_path, use_lord=False)
    engine.train()

    assert engine.training_history["stable_rank"] == []
    assert engine.training_history["rank_step"] == []


def test_reward_std_appends_per_step(tmp_path) -> None:
    """LoRD off (dense keys only): reward_std grows once per step, ≥ 0."""
    torch.manual_seed(0)
    engine = _engine(tmp_path, use_lord=False)
    engine.train()

    hist = engine.training_history
    assert len(hist["reward_std"]) == _TinyConfig.TRAIN_STEPS
    assert len(hist["reward_std"]) == len(hist["step"])
    assert all(isinstance(v, float) and v >= 0.0 for v in hist["reward_std"])


def test_checkpoint_roundtrip_preserves_new_keys(tmp_path) -> None:
    torch.manual_seed(0)
    engine = _engine(tmp_path, use_lord=True)
    engine.train()

    ckpt_path = tmp_path / "ckpt.pt"
    engine.save_checkpoint(ckpt_path, step=_TinyConfig.TRAIN_STEPS - 1)

    fresh = _engine(tmp_path, use_lord=True)
    fresh.load_checkpoint(ckpt_path)

    for key in ("rank_step", "reward_std", "stable_rank"):
        assert fresh.training_history[key] == engine.training_history[key]


def test_old_checkpoint_without_new_keys_loads_and_resumes(tmp_path) -> None:
    """A ckpt written before rank_step/reward_std existed must not KeyError
    on load or on the next appended step."""
    torch.manual_seed(0)
    engine = _engine(tmp_path, use_lord=True)
    engine.train()

    # Forge an old-format checkpoint: strip the two new keys.
    ckpt_path = tmp_path / "old.pt"
    engine.save_checkpoint(ckpt_path, step=_TinyConfig.TRAIN_STEPS - 1)
    ckpt = torch.load(ckpt_path, map_location="cpu")
    old_hist = {
        k: v
        for k, v in ckpt["training_history"].items()
        if k not in ("rank_step", "reward_std")
    }
    ckpt["training_history"] = old_hist
    torch.save(ckpt, ckpt_path)

    fresh = _engine(tmp_path, use_lord=True)
    saved_step = fresh.load_checkpoint(ckpt_path)
    assert saved_step == _TinyConfig.TRAIN_STEPS - 1

    # Merge filled the defaults; a resumed append must not raise.
    assert fresh.training_history["rank_step"] == []
    assert fresh.training_history["reward_std"] == []

    fresh.config.TRAIN_STEPS = _TinyConfig.TRAIN_STEPS + 2
    fresh.train(start_step=saved_step + 1)  # appends — no KeyError
    # Steps 5 and 6: neither hits step%100, so the stripped rank_step stays
    # empty; reward_std (dense) gains exactly the resumed steps.
    assert fresh.training_history["rank_step"] == []
    assert len(fresh.training_history["reward_std"]) == 2

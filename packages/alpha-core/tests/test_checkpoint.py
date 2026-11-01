"""Engine checkpoint round-trip + resume-from-step (follow-up: 断点续训).

Locks the new ``save_checkpoint`` / ``load_checkpoint`` pair and the
``train(start_step=..., checkpoint_path=..., checkpoint_interval=...)``
contract:
- a checkpoint survives a fresh-engine load (state_dict, best, history)
- ``start_step`` makes the loop resume instead of replaying the prefix
- periodic + final checkpoint files land on disk
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
    """Minimal loader: a constant feature tensor + raw data cache + target ret.

    ``liquidity`` must be present (and above the backtest's min_liq floor):
    with action masking every sampled formula is valid, so the REINFORCE loop
    now really reaches MemeBacktest.evaluate — the pre-mask deadlock meant
    an empty cache never got read.
    """

    def __init__(self) -> None:
        self.feat_tensor = torch.randn(3, 4, 50)
        self.raw_data_cache: dict[str, object] = {"liquidity": torch.full((3, 50), 1e6)}
        self.target_ret = torch.randn(50)
        self.loaded = False

    def load_data(self) -> None:
        self.loaded = True


def _engine(tmp_path, *, use_lord: bool = False) -> AlphaEngine:
    return AlphaEngine(
        data_loader=_FakeLoader(),
        config=_TinyConfig(),
        use_lord_regularization=use_lord,
        output_dir=str(tmp_path),
    )


def test_save_load_roundtrip_restores_state(tmp_path) -> None:
    torch.manual_seed(0)
    engine = _engine(tmp_path)
    ckpt_path = tmp_path / "ckpt.pt"
    engine.save_checkpoint(ckpt_path, step=3)

    fresh = _engine(tmp_path)
    fresh.load_checkpoint(ckpt_path)

    # Re-loaded state_dict must match exactly (shapes + weights).
    for (n1, p1), (n2, p2) in zip(
        engine.model.state_dict().items(), fresh.model.state_dict().items()
    ):
        assert n1 == n2
        assert torch.equal(p1, p2)

    assert fresh.best_score == engine.best_score
    assert fresh.best_formula == engine.best_formula


def test_load_checkpoint_returns_saved_step(tmp_path) -> None:
    engine = _engine(tmp_path)
    ckpt_path = tmp_path / "ckpt.pt"
    engine.save_checkpoint(ckpt_path, step=3)
    fresh = _engine(tmp_path)
    assert fresh.load_checkpoint(ckpt_path) == 3


def test_train_resumes_from_start_step(tmp_path) -> None:
    """A checkpoint saved at step N makes a fresh engine resume at N+1 —
    the already-trained prefix [0..N] is loaded, not replayed."""
    torch.manual_seed(1)
    ckpt = tmp_path / "ckpt.pt"

    # Phase 1: interrupt mid-run — save a checkpoint at step 2.
    engine = _engine(tmp_path)
    calls1: list[int] = []

    def _cb(step, *_: object) -> None:
        calls1.append(step)
        if step == 2:
            engine.save_checkpoint(ckpt, step)

    engine.train(on_step=_cb)
    assert calls1 == [0, 1, 2, 3, 4]

    # Phase 2: fresh engine resumes from step 3 — no replay of [0,1,2].
    fresh = _engine(tmp_path)
    resumed_step = fresh.load_checkpoint(ckpt)
    assert resumed_step == 2
    calls2: list[int] = []
    fresh.train(
        on_step=lambda step, *_: calls2.append(step), start_step=resumed_step + 1
    )
    assert calls2 == [3, 4]
    # History continues from the loaded prefix, not duplicated.
    assert fresh.training_history["step"] == [0, 1, 2, 3, 4]


def test_checkpoint_files_landed_on_disk(tmp_path) -> None:
    engine = _engine(tmp_path)
    ckpt = tmp_path / "ckpt.pt"
    engine.train(checkpoint_path=ckpt, checkpoint_interval=2)
    assert ckpt.exists()
    # Final save uses TRAIN_STEPS-1 as the last step.
    saved = torch.load(ckpt, map_location="cpu")
    assert saved["step"] == _TinyConfig.TRAIN_STEPS - 1
    assert saved["config"]["TRAIN_STEPS"] == _TinyConfig.TRAIN_STEPS

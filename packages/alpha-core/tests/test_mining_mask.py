"""Arity-aware action masking: every sampled formula is StackVM-valid.

Locks the deadlock fix — pre-mask, random 12-token formulas were valid at
~5e-05 so REINFORCE never saw a gradient (all rewards -5.0, Adam exp_avg 0).
"""

from __future__ import annotations

import torch

from selene_space_alpha.config import AlphaCoreConfig
from selene_space_alpha.mining.engine import AlphaEngine, _build_mask_table
from selene_space_alpha.ops.ops import OPS_CONFIG
from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_alpha.vm import StackVM


class _TinyConfig(AlphaCoreConfig):
    DEVICE = torch.device("cpu")
    BATCH_SIZE = 8
    TRAIN_STEPS = 2
    MAX_FORMULA_LEN = 12


class _FakeLoader:
    def __init__(self) -> None:
        self.feat_tensor = torch.randn(3, 6, 64)
        self.raw_data_cache: dict[str, object] = {"liquidity": torch.full((3, 64), 1e6)}
        self.target_ret = torch.randn(3, 64)

    def load_data(self) -> None:
        pass


def test_mask_table_final_depth_is_one_for_all_paths() -> None:
    """Uniform sampling over the masked table must yield only valid formulas."""
    max_len, n_feat = 12, 6
    off = FORMULA_VOCAB.feature_count
    table = _build_mask_table(max_len, n_feat, off, FORMULA_VOCAB.size)
    vm = StackVM()
    feat = torch.randn(4, n_feat, 100)

    torch.manual_seed(7)
    for _ in range(500):
        depth, toks = 0, []
        for t in range(max_len):
            legal = table[max_len - t, depth]
            choices = legal.nonzero().flatten()
            assert len(choices) > 0, f"empty legal set at (r={max_len - t}, d={depth})"
            tok = int(choices[torch.randint(len(choices), (1,))].item())
            toks.append(tok)
            depth += 1 if tok < n_feat else 1 - OPS_CONFIG[tok - off][2]
        assert (
            vm.execute(toks, feat) is not None
        ), f"masked-sampled formula rejected: {FORMULA_VOCAB.decode(toks)}"


def test_mask_excludes_dead_and_out_of_range_tokens() -> None:
    max_len, n_feat = 12, 6
    off = FORMULA_VOCAB.feature_count
    table = _build_mask_table(max_len, n_feat, off, FORMULA_VOCAB.size)

    # Feature ids beyond the runtime feature width are never legal.
    assert not table[:, :, n_feat : FORMULA_VOCAB.feature_count].any()
    # Operators absent from OPS_CONFIG (TS_STD_20 / IND_NEUTRALIZE) are
    # unreachable by the StackVM and stay masked everywhere.
    dead = [off + i for i in range(len(OPS_CONFIG), len(FORMULA_VOCAB.operator_names))]
    for tok in dead:
        assert not table[:, :, tok].any(), f"dead token {tok} exposed by mask"


def test_short_training_escapes_deadlock(tmp_path) -> None:
    """Real rewards diverge (not constant -5.0) and gradients flow."""
    torch.manual_seed(3)
    engine = AlphaEngine(
        data_loader=_FakeLoader(),
        config=_TinyConfig(),
        use_lord_regularization=False,
        # Was "data/tmp_mask_test" — a relative path that dropped a stray
        # best_meme_strategy.json into the repo on every run. Keep it in tmp.
        output_dir=str(tmp_path),
    )
    engine.train()

    rewards = engine.training_history["avg_reward"]
    assert any(
        abs(v + 5.0) > 1e-9 for v in rewards
    ), f"avg_reward still pinned at the deadlock value: {rewards}"
    assert any(s > 1e-6 for s in engine.training_history["reward_std"])
    assert any(
        float(state["exp_avg"].abs().sum()) > 0
        for state in engine.opt.state.values()
        if "exp_avg" in state
    ), "Adam exp_avg is zero — gradients never flowed"
    assert engine.best_formula is not None

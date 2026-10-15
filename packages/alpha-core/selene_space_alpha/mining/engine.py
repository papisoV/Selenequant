"""Alpha mining engine - REINFORCE training loop for formula discovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import time

import torch
from torch.distributions import Categorical
from tqdm import tqdm

from selene_space_core.infra.atomic_write import atomic_write

from ..config import AlphaCoreConfig
from ..ops.ops import OPS_CONFIG
from ..vm import StackVM
from .backtest import MemeBacktest
from .low_rank import LowRankRegularizer, EffectiveRankMonitor
from .model import FormulaPolicyNet


class TrainingStopRequested(Exception):
    """Raised from on_step when a cooperative stop was requested.

    A dedicated type (not StopIteration) so it survives crossing an
    asyncio executor Future — StopIteration is swallowed by asyncio's
    Future machinery (PEP 479) and would hang the awaiting coroutine.
    """


def _build_mask_table(
    max_len: int, n_features: int, feat_offset: int, vocab_size: int
) -> torch.Tensor:
    """Precompute per-(remaining_steps, stack_depth) legality as a dense bool
    tensor of shape ``[max_len+1, max_len+2, vocab_size]``.

    ``table[r, d, token]`` is True iff emitting ``token`` at stack depth ``d``
    with ``r`` slots still to sample can still finish as a valid formula
    (exactly one value left on the stack when the sequence ends). Operator
    ids come from OPS_CONFIG order; operators absent from it (e.g.
    TS_STD_20 / IND_NEUTRALIZE) are unreachable by the StackVM and stay
    masked out. Only the first ``n_features`` feature ids are legal — the
    StackVM rejects features beyond the runtime feature width.

    Reachability DP (backwards over remaining steps): ``can_finish[r][d]``
    iff some legal move from (d, r) reaches a finishable state, anchored at
    ``can_finish[0][1] = True``. Guarantees the per-step legal set is never
    empty and every sampled formula is StackVM-valid by construction.
    """
    op_arity: dict[int, int] = {
        feat_offset + i: cfg[2] for i, cfg in enumerate(OPS_CONFIG)
    }

    can_finish = torch.zeros(max_len + 1, max_len + 2, dtype=torch.bool)
    can_finish[0][1] = True
    for r in range(1, max_len + 1):
        for d in range(0, max_len + 1):
            ok = n_features > 0 and bool(can_finish[r - 1][d + 1])
            if not ok:
                for tok, arity in op_arity.items():
                    if d >= arity and bool(can_finish[r - 1][d - arity + 1]):
                        ok = True
                        break
            can_finish[r][d] = ok

    table = torch.zeros(max_len + 1, max_len + 2, vocab_size, dtype=torch.bool)
    for r in range(1, max_len + 1):
        for d in range(0, max_len + 1):
            if not can_finish[r][d]:
                continue
            if n_features > 0 and bool(can_finish[r - 1][d + 1]):
                table[r, d, :n_features] = True
            for tok, arity in op_arity.items():
                if d >= arity and bool(can_finish[r - 1][d - arity + 1]):
                    table[r, d, tok] = True
    return table


class AlphaEngine:
    """REINFORCE training engine for formula discovery.

    Generates formula token sequences via the FormulaPolicyNet Transformer,
    evaluates them via StackVM + MemeBacktest, and optimizes
    the generator using policy gradient.
    """

    def __init__(
        self,
        data_loader=None,
        config: AlphaCoreConfig | None = None,
        use_lord_regularization: bool = True,
        output_dir: str | Path = ".",
    ):
        self.config = config or AlphaCoreConfig()
        self.loader = data_loader
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.model = FormulaPolicyNet(self.config).to(self.config.DEVICE)

        self.opt = torch.optim.AdamW(self.model.parameters(), lr=1e-3)

        self.use_lord = use_lord_regularization
        self.lord_opt: LowRankRegularizer | None = None
        self.rank_monitor: EffectiveRankMonitor | None = None
        if self.use_lord:
            self.lord_opt = LowRankRegularizer(
                self.model.named_parameters(),
                decay_rate=self.config.LORD_DECAY_RATE,
                num_iterations=self.config.LORD_NUM_ITERATIONS,
                target_keywords=self.config.LORD_TARGET_KEYWORDS,
            )
            self.rank_monitor = EffectiveRankMonitor(
                self.model,
                target_keywords=["q_proj", "k_proj"],
            )

        self.vm = StackVM()
        self.bt = MemeBacktest()

        self.best_score = -float("inf")
        self.best_formula: list[int] | None = None
        self.training_history: dict[str, list] = {
            "step": [],
            "avg_reward": [],
            "best_score": [],
            # 每步批次奖励的 std(候选分布宽度) — 优势带的机械底座
            "reward_std": [],
            # stable_rank 是稀疏序列(仅 use_lord 且 step%100==0 才采样),
            # rank_step 与它等长,zip 即 [(step, rank)] — 前端对齐走契约而非 step%100 约定
            "stable_rank": [],
            "rank_step": [],
        }

    def save_checkpoint(
        self, path: str | Path, step: int, extra: dict | None = None
    ) -> None:
        """Persist training state so a later session can resume from ``step``.

        Captures model + optimizer state, the running best, the history, and
        enough config to rebuild the engine. LoRD regularizer is stateless
        (recomputed from parameters each ``step()``), so it needs no save.
        ``extra`` carries backend-owned metadata (e.g. the full training
        request) that the engine itself has no knowledge of.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "model": self.model.state_dict(),
                "opt": self.opt.state_dict(),
                "step": step,
                "best_score": self.best_score,
                "best_formula": self.best_formula,
                "training_history": self.training_history,
                "config": {
                    "TRAIN_STEPS": self.config.TRAIN_STEPS,
                    "BATCH_SIZE": self.config.BATCH_SIZE,
                    "MAX_FORMULA_LEN": self.config.MAX_FORMULA_LEN,
                },
                "use_lord": self.use_lord,
                "extra": extra or {},
            },
            path,
        )

    def load_checkpoint(self, path: str | Path) -> int:
        """Load a checkpoint written by ``save_checkpoint``; returns the step.

        Restores model + optimizer state, best score/formula, and history.
        Backend-owned metadata saved as ``extra`` is exposed on
        ``self.checkpoint_extra``. The caller is expected to have constructed
        the engine with the same config (so model/optimizer shapes match).
        """
        ckpt = torch.load(Path(path), map_location=self.config.DEVICE)
        self.model.load_state_dict(ckpt["model"])
        self.opt.load_state_dict(ckpt["opt"])
        self.best_score = ckpt["best_score"]
        self.best_formula = ckpt.get("best_formula")
        # Merge, not replace: a checkpoint written before ``reward_std`` /
        # ``rank_step`` existed keeps loading, and the resumed loop finds the
        # (empty) default lists instead of KeyError-ing on append.
        history = dict(self.training_history)
        history.update(ckpt.get("training_history", {}))
        self.training_history = history
        self.checkpoint_extra = ckpt.get("extra") or {}
        return int(ckpt.get("step", 0))

    def train(
        self,
        on_step: Callable[[int, float, float, float | None, str | None], None]
        | None = None,
        start_step: int = 0,
        checkpoint_path: str | Path | None = None,
        checkpoint_interval: int = 10,
    ) -> None:
        """Run the REINFORCE training loop.

        ``on_step`` (optional) is invoked once per step with
        ``(step, avg_reward, best_score, stable_rank, log_line)``. ``stable_rank``
        is ``None`` unless LoRD regularization is active and ``step % 100 == 0``;
        ``log_line`` carries the latest human-readable progress line (may be
        ``None`` when nothing changed). Default ``None`` keeps zero behavior
        change for existing callers.

        ``start_step`` resumes an interrupted run: the loop only executes steps
        ``[start_step, TRAIN_STEPS)`` so an already-trained prefix is not
        replayed. When ``checkpoint_path`` is given, state is persisted every
        ``checkpoint_interval`` steps (and once at the end) so a crash can
        resume from the last full step.
        """
        if self.loader is None:
            raise RuntimeError("No data_loader provided. Call with data_loader first.")

        self.loader.load_data()

        # Arity-aware action masking, matched to the runtime feature width:
        # the StackVM rejects feature ids beyond feat_tensor.shape[1] and
        # operators absent from OPS_CONFIG, so both families must stay masked.
        n_feat = int(self.loader.feat_tensor.shape[1])
        mask_table = _build_mask_table(
            self.config.MAX_FORMULA_LEN,
            n_feat,
            self.vm.feat_offset,
            self.model.vocab_size,
        ).to(self.config.DEVICE)
        # Stack-depth delta per token: feature +1, operator arity a → 1-a.
        deltas = torch.full(
            (self.model.vocab_size,), 0, dtype=torch.long, device=self.config.DEVICE
        )
        deltas[:n_feat] = 1
        for i, cfg in enumerate(OPS_CONFIG):
            deltas[self.vm.feat_offset + i] = 1 - cfg[2]

        desc = "Mining Alpha Formulas"
        if self.use_lord:
            desc += " (LoRD)"
        pbar = tqdm(range(start_step, self.config.TRAIN_STEPS), desc=desc)

        sample_ms_total = 0.0
        eval_ms_total = 0.0

        for step in pbar:
            bs = self.config.BATCH_SIZE
            inp = torch.zeros((bs, 1), dtype=torch.long, device=self.config.DEVICE)

            log_probs = []
            tokens_list = []
            depths = torch.zeros(bs, dtype=torch.long, device=self.config.DEVICE)

            t0 = time.perf_counter()
            for t in range(self.config.MAX_FORMULA_LEN):
                remaining = self.config.MAX_FORMULA_LEN - t
                logits, _, _ = self.model(inp)
                legal = mask_table[remaining][depths]
                logits = logits.masked_fill(~legal, float("-inf"))
                dist = Categorical(logits=logits)
                action = dist.sample()

                log_probs.append(dist.log_prob(action))
                tokens_list.append(action)
                inp = torch.cat([inp, action.unsqueeze(1)], dim=1)
                depths = depths + deltas[action]
            sample_ms = (time.perf_counter() - t0) * 1000
            sample_ms_total += sample_ms

            seqs = torch.stack(tokens_list, dim=1)
            rewards = torch.zeros(bs, device=self.config.DEVICE)

            t0 = time.perf_counter()
            for i in range(bs):
                formula = seqs[i].tolist()
                res = self.vm.execute(formula, self.loader.feat_tensor)

                if res is None:
                    rewards[i] = -5.0
                    continue

                if res.std() < 1e-4:
                    rewards[i] = -2.0
                    continue

                score, ret_val = self.bt.evaluate(
                    res, self.loader.raw_data_cache, self.loader.target_ret
                )
                rewards[i] = score

                if score.item() > self.best_score:
                    self.best_score = score.item()
                    self.best_formula = formula
                    tqdm.write(
                        f"[!] New Best: Score {score:.2f} | "
                        f"Ret {ret_val:.2%} | Formula {formula}"
                    )
            eval_ms = (time.perf_counter() - t0) * 1000
            eval_ms_total += eval_ms

            adv = (rewards - rewards.mean()) / (rewards.std() + 1e-5)

            loss = torch.zeros((), device=self.config.DEVICE)
            for t in range(len(log_probs)):
                loss = loss + (-log_probs[t] * adv)
            loss = loss.mean()

            self.opt.zero_grad()
            loss.backward()
            self.opt.step()

            if self.use_lord:
                assert self.lord_opt is not None
                self.lord_opt.step()

            avg_reward = rewards.mean().item()
            postfix = {
                "AvgRew": f"{avg_reward:.3f}",
                "Best": f"{self.best_score:.3f}",
                "sample": f"{sample_ms:.0f}ms",
                "eval": f"{eval_ms:.0f}ms",
            }

            self.training_history["reward_std"].append(rewards.std().item())

            stable_rank: float | None = None
            if self.use_lord and step % 100 == 0:
                assert self.rank_monitor is not None
                stable_rank = self.rank_monitor.compute()
                postfix["Rank"] = f"{stable_rank:.2f}"
                self.training_history["stable_rank"].append(stable_rank)
                self.training_history["rank_step"].append(step)

            self.training_history["step"].append(step)
            self.training_history["avg_reward"].append(avg_reward)
            self.training_history["best_score"].append(self.best_score)

            log_line = (
                f"[!] New Best: Score {self.best_score:.2f} | "
                f"Formula {self.best_formula}"
                if self.best_formula is not None
                else None
            )
            if on_step is not None:
                on_step(step, avg_reward, self.best_score, stable_rank, log_line)

            pbar.set_postfix(postfix)

            if checkpoint_path is not None and (step + 1) % checkpoint_interval == 0:
                self.save_checkpoint(checkpoint_path, step)

        if checkpoint_path is not None:
            self.save_checkpoint(checkpoint_path, self.config.TRAIN_STEPS - 1)

        if steps_completed := len(self.training_history["step"]) - start_step:
            tqdm.write(
                f"[timing] {steps_completed} steps | "
                f"sample avg {sample_ms_total / steps_completed:.0f} ms/step | "
                f"eval avg {eval_ms_total / steps_completed:.0f} ms/step "
                f"(batch {self.config.BATCH_SIZE}, masked sampling)"
            )

        self._save_results()

    def _save_results(self) -> None:
        if self.best_formula is not None:
            atomic_write(
                self.output_dir / "best_meme_strategy.json",
                json.dumps(self.best_formula),
            )

        atomic_write(
            self.output_dir / "training_history.json",
            json.dumps(self.training_history),
        )

        print(f"\nTraining complete! Best score: {self.best_score:.4f}")
        if self.best_formula:
            print(f"Best formula: {self.best_formula}")

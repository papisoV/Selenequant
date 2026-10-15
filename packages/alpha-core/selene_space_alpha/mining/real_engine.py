"""Alpha mining engine with real backtest fitness.

Same REINFORCE loop as AlphaEngine, but uses RealBacktestFitness
instead of MemeBacktest for formula evaluation. This makes the
training directly optimize real trading metrics (Sharpe, win rate, etc.).
"""

from __future__ import annotations

import json
from pathlib import Path

import torch
from torch.distributions import Categorical
from tqdm import tqdm

from selene_space_core.infra.atomic_write import atomic_write

from ..config import AlphaCoreConfig
from ..vm import StackVM
from ..vocab import FormulaVocab
from .model import FormulaPolicyNet
from .real_fitness import RealBacktestFitness


class RealFitnessAlphaEngine:
    """REINFORCE engine with real backtest fitness evaluation.

    Same architecture as AlphaEngine but evaluates formulas by running
    actual BacktestStrategyTemplate backtests on real K-line data.

    ``synthetic_gate`` (default ``False``) preserves the legacy zeros-tensor
    VM pre-check for side-by-side comparison. On real data that gate judges
    every valid formula constant (``res.std() < 1e-4``), so it is a known
    defect and is OFF by default; the deterministic adapter
    (``deterministic_fitness.py``) carries its own ``validate()`` +
    ``std < 1e-8`` sentinel, so skipping the gate is safe.
    """

    def __init__(
        self,
        vocab: FormulaVocab,
        fitness: RealBacktestFitness,
        config: AlphaCoreConfig | None = None,
        use_lord_regularization: bool = True,
        synthetic_gate: bool = False,
        output_dir: str | Path = ".",
        warm_start_formulas: list[list[int]] | None = None,
    ):
        self.config = config or AlphaCoreConfig()
        self.vocab = vocab
        self.fitness = fitness
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.warm_start_formulas = warm_start_formulas
        self.synthetic_gate = synthetic_gate

        self.model = FormulaPolicyNet(self.config).to(self.config.DEVICE)
        # Override vocab in model
        self.model.features_list = list(vocab.feature_names)
        self.model.ops_list = list(vocab.operator_names)
        self.model.vocab = list(vocab.token_names)
        self.model.vocab_size = vocab.size
        self.model.token_emb = torch.nn.Embedding(vocab.size, self.model.d_model).to(
            self.config.DEVICE
        )
        self.model.mtp_head._orig_init = True  # type: ignore[assignment,union-attr]  # reset MTP head for new vocab size

        # Rebuild MTP head and critic for new vocab size
        d_model = self.model.d_model
        num_tasks = 3
        self.model.mtp_head.task_heads = torch.nn.ModuleList(
            [torch.nn.Linear(d_model, vocab.size) for _ in range(num_tasks)]
        ).to(self.config.DEVICE)
        self.model.mtp_head.task_router = torch.nn.Sequential(
            torch.nn.Linear(d_model, d_model // 2),
            torch.nn.ReLU(),
            torch.nn.Linear(d_model // 2, num_tasks),
        ).to(self.config.DEVICE)
        self.model.head_critic = torch.nn.Linear(d_model, 1).to(self.config.DEVICE)

        self.opt = torch.optim.AdamW(self.model.parameters(), lr=1e-3)

        self.use_lord = use_lord_regularization
        if self.use_lord:
            from .low_rank import LowRankRegularizer, EffectiveRankMonitor

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
        else:
            self.lord_opt = None  # type: ignore[assignment]
            self.rank_monitor = None  # type: ignore[assignment]

        self.vm = StackVM(vocab)

        self.best_score = -float("inf")
        self.best_formula: list[int] | None = None
        self.training_history: dict[str, list] = {
            "step": [],
            "avg_reward": [],
            "best_score": [],
        }

    def _evaluate_formula_batch(
        self, seqs: torch.Tensor, batch_size: int
    ) -> torch.Tensor:
        """Evaluate a batch of formula sequences using real backtest fitness."""
        rewards = torch.zeros(batch_size, device=self.config.DEVICE)

        for i in range(batch_size):
            formula = seqs[i].tolist()
            score = None

            # Legacy zeros-tensor VM pre-check: only runs when
            # synthetic_gate=True. On real K-lines it judges every valid
            # formula constant (std < 1e-4) because the VM executes on a zero
            # tensor — a known defect — so it is OFF by default. The
            # deterministic adapter carries its own validate() + std < 1e-8
            # sentinel, making the skip safe.
            if self.synthetic_gate:
                dummy = torch.zeros(3, self.vocab.feature_count, 100)
                res = self.vm.execute(formula, dummy)
                if res is None:
                    rewards[i] = -5.0
                    continue
                if res.std() < 1e-4:
                    rewards[i] = -3.0
                    continue

            # Real backtest evaluation
            try:
                score = self.fitness.evaluate(formula, self.vocab)
                rewards[i] = score
            except Exception:
                rewards[i] = -5.0

            if score is not None and score > self.best_score:
                self.best_score = score
                self.best_formula = formula
                readable = self.vocab.decode(formula)
                tqdm.write(
                    f"[!] New Best: Sharpe-fitness {score:.3f} | " f"Formula {readable}"
                )

        return rewards

    def train(self) -> None:
        """Run the REINFORCE training loop with real backtest fitness.

        When ``warm_start_formulas`` is set, the first few steps inject
        warm-start formulas into the batch to bias the policy toward
        proven alpha sources.
        """
        desc = "Mining Alpha (Real Backtest Fitness)"
        if self.use_lord:
            desc += " (LoRD)"
        if self.warm_start_formulas:
            desc += f" (WarmStart:{len(self.warm_start_formulas)})"
        pbar = tqdm(range(self.config.TRAIN_STEPS), desc=desc)

        warm_start_steps = 10 if self.warm_start_formulas else 0

        for step in pbar:
            bs = self.config.BATCH_SIZE
            # Use smaller batch for real backtest (expensive)
            eval_batch = min(bs, 32)
            inp = torch.zeros(
                (eval_batch, 1), dtype=torch.long, device=self.config.DEVICE
            )

            log_probs = []
            tokens_list = []

            for _ in range(self.config.MAX_FORMULA_LEN):
                logits, _, _ = self.model(inp)
                dist = Categorical(logits=logits)
                action = dist.sample()

                log_probs.append(dist.log_prob(action))
                tokens_list.append(action)
                inp = torch.cat([inp, action.unsqueeze(1)], dim=1)

            seqs = torch.stack(tokens_list, dim=1)

            # Inject warm-start formulas into early training steps
            if step < warm_start_steps and self.warm_start_formulas:
                n_inject = min(len(self.warm_start_formulas), eval_batch // 2)
                max_len = self.config.MAX_FORMULA_LEN
                for i, ws in enumerate(self.warm_start_formulas[:n_inject]):
                    padded = ws[:max_len]
                    while len(padded) < max_len:
                        padded = padded + [0]
                    for t in range(max_len):
                        seqs[i, t] = padded[t]

            # Evaluate with real backtests
            rewards = self._evaluate_formula_batch(seqs, eval_batch)

            # REINFORCE gradient
            adv = (rewards - rewards.mean()) / (rewards.std() + 1e-5)

            loss = 0
            for t in range(len(log_probs)):
                loss += -log_probs[t] * adv
            loss = loss.mean()  # type: ignore[union-attr,attr-defined]

            self.opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
            self.opt.step()

            if self.use_lord and self.lord_opt:
                self.lord_opt.step()

            avg_reward = rewards.mean().item()
            postfix = {"AvgRew": f"{avg_reward:.3f}", "Best": f"{self.best_score:.3f}"}

            self.training_history["step"].append(step)
            self.training_history["avg_reward"].append(avg_reward)
            self.training_history["best_score"].append(self.best_score)

            pbar.set_postfix(postfix)

        self._save_results()

    def _save_results(self) -> None:
        atomic_write(
            self.output_dir / "training_history.json",
            json.dumps(self.training_history),
        )
        if self.best_formula is not None:
            atomic_write(
                self.output_dir / "best_meme_strategy.json",
                json.dumps(self.best_formula),
            )

        print(f"\nTraining complete! Best fitness: {self.best_score:.4f}")
        if self.best_formula:
            print(f"Best formula: {self.vocab.decode(self.best_formula)}")

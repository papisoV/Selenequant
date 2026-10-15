"""RL engine + deterministic adapter (WS-B): lock the RealFitnessAlphaEngine contract.

Covers the mixed exploitation layer (US-06/US-07): a torch-free
``DeterministicFitnessAdapter`` feeding the REINFORCE engine's ``FitnessProtocol``
so the RL policy optimizes the *same* composite the deterministic search uses.

- **Adapter contract** — GOOD scores a finite ``float``, degenerate / invalid
  formulas return ``floor=-5.0``, and ``score_kwargs`` reach ``score``.
- **Engine trains (cold start)** — a tiny CPU config completes ``train()``,
  records 3 steps, and persists ``training_history.json``. Cold-start random
  sampling scores most candidates at ``floor`` (random length-6 formulas over
  the vocab are valid RPN ~2.6%, finite-scoring ~0.6%) — the no-gradient trap —
  so the *usable-reward* proof lives in the warm-start test.
- **Warm-start injection** — a valid length-6 formula scores ``> floor``, so the
  batch always contains a usable reward (the O2 no-gradient dependency).
- **synthetic_gate flag** — OFF returns the adapter's real score for a valid
  formula; ON reproduces the legacy zeros-tensor verdict (-3.0).
- **Exception path** — a raising fitness yields ``-5.0`` with no ``NameError``
  (the best-update is guarded on ``score is not None``).

Runtime budget: n_dates=40 / n_symbols=2 panel, ~0.1-0.3s per candidate; the two
``train()`` runs dominate. Whole file targets < 60s.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest
import torch

from selene_space_alpha.config import AlphaCoreConfig
from selene_space_alpha.mining.real_engine import RealFitnessAlphaEngine
from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_factor_research.mining.deterministic_fitness import (
    DeterministicFitnessAdapter,
)

RET = list(FORMULA_VOCAB.token_names).index("RET")
TS_RANK = list(FORMULA_VOCAB.token_names).index("TS_RANK")
SUB = list(FORMULA_VOCAB.token_names).index("SUB")
LIQ = list(FORMULA_VOCAB.token_names).index("LIQ_SCORE")
ABS = list(FORMULA_VOCAB.token_names).index("ABS")

GOOD = [RET, TS_RANK]
DEGENERATE = [LIQ, ABS]
#: Valid MAX_FORMULA_LEN=6 momentum formula: TS_RANK(RET) - (RET - RET) == TS_RANK(RET).
#: Length 6 so the engine's warm-start padding (fill with token 0 = RET) is a no-op.
#: (A short formula like GOOD padded to 6 becomes [RET,TS_RANK,RET,RET,RET,RET], which
#: `validate` rejects — the engine's padding is a pre-existing defect, documented in
#: the warm-start test below.)
MOM6 = [RET, TS_RANK, RET, RET, SUB, SUB]


class _TinyConfig(AlphaCoreConfig):
    DEVICE = torch.device("cpu")
    BATCH_SIZE = 8
    TRAIN_STEPS = 3
    MAX_FORMULA_LEN = 6


class _RaisingFitness:
    """FitnessProtocol-shaped stub whose ``evaluate`` always raises."""

    def evaluate(self, formula_tokens: list[int], vocab: object) -> float:
        raise RuntimeError("backtest boom")


def _panels() -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    """Small homogeneous AR(1) momentum panel (n_dates=200, n_symbols=2).

    (n_dates 40→200 when _robust_norm went trailing-window: the rolling
    warmup eats the first ~20 bars, and a 40-bar panel no longer carries
    enough live bars past the 70%-train split to score above floor.)
    """
    rng = np.random.default_rng(42)
    n, ns = 200, 2
    dates = pd.bdate_range(start="2024-01-01", periods=n)
    noise = rng.normal(0.0, 0.02, (n, ns))
    rets = np.zeros_like(noise)
    rets[0] = noise[0]
    for t in range(1, n):
        rets[t] = 0.5 * rets[t - 1] + noise[t]
    close = 100.0 * np.cumprod(1.0 + rets, axis=0)
    open_ = close / (1.0 + rets)
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    data: dict[str, pd.DataFrame] = {}
    ret_by_symbol: dict[str, pd.Series] = {}
    for s in range(ns):
        sym = f"S{s}"
        data[sym] = pd.DataFrame(
            {
                "open": open_[:, s],
                "high": high[:, s],
                "low": low[:, s],
                "close": close[:, s],
                "volume": 1000.0,
            },
            index=dates,
        )
        closes = pd.Series(close[:, s], index=dates)
        ret_by_symbol[sym] = closes.shift(-1) / closes - 1.0
    return data, ret_by_symbol


@pytest.fixture(scope="module")
def panels() -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    return _panels()


def _adapter(panels: tuple[dict, dict]) -> DeterministicFitnessAdapter:
    data, ret = panels
    return DeterministicFitnessAdapter(data, ret)


def _engine(
    adapter: object,
    tmp_path: pytest.TempPathFactory | None = None,
    *,
    synthetic_gate: bool = False,
    warm_start_formulas: list[list[int]] | None = None,
) -> RealFitnessAlphaEngine:
    return RealFitnessAlphaEngine(
        FORMULA_VOCAB,
        adapter,  # type: ignore[arg-type]  # duck-typed FitnessProtocol
        config=_TinyConfig(),
        use_lord_regularization=False,
        synthetic_gate=synthetic_gate,
        output_dir=str(tmp_path) if tmp_path is not None else ".",
        warm_start_formulas=warm_start_formulas,
    )


def _seq_row(tokens: list[int]) -> torch.Tensor:
    return torch.tensor([tokens], dtype=torch.long)


# --------------------------------------------------------------------------
# Adapter contract (US-06 criterion 5).
# --------------------------------------------------------------------------
def test_adapter_good_scores_finite(panels) -> None:
    adapter = _adapter(panels)
    s = adapter.evaluate(GOOD, FORMULA_VOCAB)
    assert math.isfinite(s)
    assert s > adapter.floor


def test_adapter_degenerate_returns_floor(panels) -> None:
    adapter = _adapter(panels)
    assert adapter.evaluate(DEGENERATE, FORMULA_VOCAB) == adapter.floor


def test_adapter_invalid_sentinel_returns_floor(panels) -> None:
    adapter = _adapter(panels)
    # Structure-invalid padded form (the engine's warm-start padding shape) →
    # score() sentinel (valid=False, score=-inf) → floor.
    assert adapter.evaluate(GOOD + [RET, RET, RET, RET], FORMULA_VOCAB) == adapter.floor


def test_adapter_score_kwargs_passthrough(panels) -> None:
    adapter = _adapter(panels)
    zeroed = DeterministicFitnessAdapter(
        panels[0], panels[1], score_kwargs={"w_ic": 0.0, "w_sharpe": 0.0}
    )
    assert zeroed.evaluate(GOOD, FORMULA_VOCAB) != adapter.evaluate(GOOD, FORMULA_VOCAB)


# --------------------------------------------------------------------------
# Engine integration (US-07).
# --------------------------------------------------------------------------
def test_engine_with_adapter_trains(panels, tmp_path) -> None:
    torch.manual_seed(42)
    torch.set_num_threads(1)
    engine = _engine(_adapter(panels), tmp_path, synthetic_gate=False)
    engine.train()  # must complete without exception
    assert len(engine.training_history["step"]) == 3
    # Cold-start random sampling scored every candidate at floor on this seed:
    # random length-6 formulas over the vocab are valid RPN ~2.6% and finite-
    # scoring ~0.6%, so a 24-sample cold batch landing all-floor is expected.
    # That constant-reward batch IS the no-gradient trap O2 documents — the
    # usable-reward / learning proof lives in test_warm_start_injection.
    history = json.loads((tmp_path / "training_history.json").read_text())
    assert len(history["step"]) == 3
    assert (tmp_path / "best_meme_strategy.json").exists()


def test_warm_start_injection(panels, tmp_path) -> None:
    torch.manual_seed(42)
    torch.set_num_threads(1)
    # MOM6 is valid length-6 → the injection scores > floor, guaranteeing the
    # batch contains a usable reward (the O2 no-gradient dependency). A SHORT
    # formula here would be padded with token 0 (RET) into an invalid formula
    # and score floor — the engine's padding is a known pre-existing defect.
    engine = _engine(
        _adapter(panels), tmp_path, synthetic_gate=False, warm_start_formulas=[MOM6]
    )
    engine.train()
    assert engine.best_score > -5.0  # == exists a > floor reward via injection
    assert len(engine.training_history["step"]) == 3


def test_synthetic_gate_flag(panels) -> None:
    adapter = _adapter(panels)
    seqs = _seq_row(MOM6)
    off = _engine(adapter, synthetic_gate=False)
    r_off = off._evaluate_formula_batch(seqs, 1)
    # rewards is a float32 tensor; adapter returns float64 — compare approximately.
    assert r_off[0].item() == pytest.approx(
        adapter.evaluate(MOM6, FORMULA_VOCAB), abs=1e-5
    )
    assert r_off[0].item() > -3.0
    on = _engine(adapter, synthetic_gate=True)
    r_on = on._evaluate_formula_batch(seqs, 1)
    assert r_on[0].item() == -3.0  # legacy zeros gate judges it constant


def test_evaluate_exception_path(panels) -> None:
    seqs = _seq_row(MOM6)
    engine = _engine(_RaisingFitness(), synthetic_gate=False)
    rewards = engine._evaluate_formula_batch(seqs, 1)  # must not raise NameError
    assert rewards[0].item() == -5.0
    assert engine.best_score == -float(
        "inf"
    )  # best-update guarded on score is not None

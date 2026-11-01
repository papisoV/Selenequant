"""Artifact runtime — load ``bridge:<name>`` artifacts into AlphaFormulaStrategy.

Single source of truth for the Block-A wiring: an artifact ``.py`` file's
docstring header declares its formula as *names*; this module resolves them to
selene tokens, gates the chain structurally (RPN stack reduction), applies the
mechanical threshold default, and hands back a runnable
:class:`~selene_space_alpha.strategy_bridge.AlphaFormulaStrategy`.

Design contract (design review 2026-09-18, ADR A2):

- Missing names and structurally invalid chains raise
  :class:`~selene_space_alpha.artifact_bridge.UnsupportedArtifact` — never
  degrade silently. A chain that "runs" from the wrong features reports
  wrong numbers confidently.
- Threshold default: the last operator determines the semantics.
  SIGN-terminal chains emit exactly {-1, 0, +1}, so buy/sell 0.6/0.4 maps
  sigmoid(-1/0/1)={0.269, 0.5, 0.731} back to SELL/HOLD/BUY precisely.
  Non-SIGN chains emit continuous values; 0.85/0.45 is a *placeholder* until
  the signal-wrapping follow-up lands.
- ``entry_threshold`` is deliberately NOT wired: sigmoid thresholds and the
  artifact's tanh-deadband are different constructs; passing it through
  would deterministically degrade (all sigmoid values > 0.05 → BUY every bar).
"""

from __future__ import annotations

import os
from pathlib import Path

from .artifact_bridge import (
    UnsupportedArtifact,
    names_from_py_header,
    resolve,
    to_tokens,
)
from .strategy_bridge import AlphaFormulaStrategy
from .vocab import FORMULA_VOCAB, FormulaVocab

__all__ = [
    "load_artifact_strategy",
    "rpn_stack_gate",
    "default_thresholds",
]

#: sigmoid(-1)=0.269 < 0.4 → SELL; sigmoid(0)=0.5 → HOLD; sigmoid(1)=0.731 > 0.6 → BUY
SIGN_CHAIN_BUY_TH = 0.6
SIGN_CHAIN_SELL_TH = 0.4
#: Placeholder until the signal-wrapping follow-up (tanh/zscore/deadband) lands.
CONTINUOUS_CHAIN_BUY_TH = 0.85
CONTINUOUS_CHAIN_SELL_TH = 0.45


def default_thresholds(vocab: FormulaVocab, tokens: list[int]) -> tuple[float, float]:
    """Mechanical threshold default by the chain's terminal operator."""
    terminal = vocab.token_names[tokens[-1]] if tokens else ""
    if terminal == "SIGN":
        return SIGN_CHAIN_BUY_TH, SIGN_CHAIN_SELL_TH
    return CONTINUOUS_CHAIN_BUY_TH, CONTINUOUS_CHAIN_SELL_TH


def rpn_stack_gate(vocab: FormulaVocab, tokens: list[int]) -> None:
    """Simulate stack reduction and raise if the chain does not reduce to one value.

    Features push, operators pop ``arity`` and push one. A chain that ends on a
    stack of any size other than 1 is structurally broken: the VM would return
    ``None`` mid-chain and every downstream consumer silently degrades.

    IND_NEUTRALIZE has arity -1 (DataFrame-level, consumes the whole stack);
    treated as consuming the entire remaining stack and pushing one.
    """
    arity_of = {name: arity for name, arity in _operator_arities(vocab)}
    stack = 0
    for pos, token in enumerate(tokens):
        name = (
            vocab.token_names[token]
            if 0 <= token < len(vocab.token_names)
            else f"UNK({token})"
        )
        if name in arity_of:
            arity = arity_of[name]
            if arity < 0:
                stack = 1
                continue
            if stack < arity:
                raise UnsupportedArtifact(
                    f"structurally invalid chain: operator {name} at token "
                    f"position {pos} needs {arity} operand(s) but only {stack} on stack"
                )
            stack -= arity - 1
        else:
            stack += 1
    if stack != 1:
        raise UnsupportedArtifact(
            f"structurally invalid chain: RPN reduction ends with stack size "
            f"{stack} (expected 1); chain must reduce to a single value"
        )


def _operator_arities(vocab: FormulaVocab) -> list[tuple[str, int]]:
    from .ops.registry import OPERATOR_DEFS

    return [
        (name, arity)
        for name, arity in OPERATOR_DEFS
        if name in set(vocab.operator_names)
    ]


def load_artifact_strategy(
    artifact_name: str,
    *,
    artifact_dir: Path | None = None,
    buy_th: float | None = None,
    sell_th: float | None = None,
    vocab: FormulaVocab | None = None,
) -> AlphaFormulaStrategy:
    """Load a ``bridge:`` artifact by file stem into a runnable strategy.

    Raises :class:`UnsupportedArtifact` with the full missing-name list (or the
    structural error) on any unresolvable artifact.
    """
    directory = Path(
        artifact_dir or os.environ.get("SELENE_ARTIFACT_DIR", "<local-path-redacted>")
    )
    path = directory / f"{artifact_name}.py"
    if not path.is_file():
        raise UnsupportedArtifact(
            f"artifact '{artifact_name}' not found: no file at {path} "
            f"(SELENE_ARTIFACT_DIR={directory})"
        )
    source = path.read_text(encoding="utf-8")
    names = names_from_py_header(source)
    verdicts = resolve(names, vocab=vocab or FORMULA_VOCAB)
    tokens = to_tokens(verdicts)
    active_vocab = vocab or FORMULA_VOCAB
    rpn_stack_gate(active_vocab, tokens)

    if buy_th is None or sell_th is None:
        gate_buy, gate_sell = default_thresholds(active_vocab, tokens)
        buy_th = gate_buy if buy_th is None else buy_th
        sell_th = gate_sell if sell_th is None else sell_th

    return AlphaFormulaStrategy(
        formula_tokens=tokens,
        vocab=active_vocab,
        buy_threshold=buy_th,
        sell_threshold=sell_th,
        name=artifact_name,
    )

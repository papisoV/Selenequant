"""Stack-based virtual machine for formula execution (torch-native)."""

from __future__ import annotations

import torch

from ..ops.ops import OPS_CONFIG
from ..vocab import FORMULA_VOCAB, FormulaVocab


class StackVM:
    """Executes formula token sequences on torch tensors.

    Token < operator_offset: feature index (load feat_tensor[:, token, :])
    Token >= operator_offset: operator (pop arity args, push result)
    """

    def __init__(self, vocab: FormulaVocab | None = None):
        vocab = vocab or FORMULA_VOCAB
        self.feat_offset = vocab.operator_offset
        self.op_map = {
            i + self.feat_offset: cfg[1] for i, cfg in enumerate(OPS_CONFIG)
        }
        self.arity_map = {
            i + self.feat_offset: cfg[2] for i, cfg in enumerate(OPS_CONFIG)
        }

    def execute(
        self, formula_tokens: list[int], feat_tensor: torch.Tensor
    ) -> torch.Tensor | None:
        """Execute a formula on a feature tensor.

        Args:
            formula_tokens: List of token indices.
            feat_tensor: Tensor of shape [tokens, features, timesteps].

        Returns:
            Result tensor of shape [tokens, timesteps], or None on error.
        """
        stack: list[torch.Tensor] = []
        try:
            for token in formula_tokens:
                token = int(token)
                if token < self.feat_offset:
                    if token >= feat_tensor.shape[1]:
                        return None
                    stack.append(feat_tensor[:, token, :])
                elif token in self.op_map:
                    arity = self.arity_map[token]
                    if len(stack) < arity:
                        return None
                    args = []
                    for _ in range(arity):
                        args.append(stack.pop())
                    args.reverse()
                    func = self.op_map[token]
                    res = func(*args)
                    if torch.isnan(res).any() or torch.isinf(res).any():
                        res = torch.nan_to_num(
                            res, nan=0.0, posinf=1.0, neginf=-1.0
                        )
                    stack.append(res)
                else:
                    return None
            if len(stack) == 1:
                return stack[0]
            return None
        except Exception:
            return None

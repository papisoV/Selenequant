# Cleanroom implementation: design intent derived from prior art; independent expression.

"""Autoregressive formula-policy network built on a looped Transformer.

Carries the design intent of a Transformer policy that generates formula
token sequences, implemented independently for the Selene alpha-core mining
stack; no source text from the licensed reference is reproduced. Only public architecture terms are
used: RMSNorm (Zhang & Sennrich 2019), QK-Norm (unit-normalized query/key),
SwiGLU gating (Shazeer 2020), and a looped encoder that re-applies its layers
for deeper implicit depth.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..config import AlphaCoreConfig
from ..vocab import FORMULA_VOCAB


class RMSNorm(nn.Module):
    """Root-mean-square layer normalization with a per-channel scale."""

    def __init__(self, d_model: int, eps: float = 1e-6):
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(d_model))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        rms = torch.sqrt(torch.mean(x**2, dim=-1, keepdim=True) + self.eps)
        return (x / rms) * self.weight


class QKNorm(nn.Module):
    """Unit-normalizes query and key vectors, then applies a learned scale."""

    def __init__(self, d_model: int, eps: float = 1e-6):
        super().__init__()
        self.eps = eps
        self.scale = nn.Parameter(torch.ones(1, 1, 1, d_model) * (d_model**-0.5))

    def forward(
        self, q: torch.Tensor, k: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        q_norm = F.normalize(q, p=2, dim=-1)
        k_norm = F.normalize(k, p=2, dim=-1)
        return q_norm * self.scale, k_norm * self.scale


class SwiGLU(nn.Module):
    """Gated feed-forward block: ``silu``-gated projection then output mix."""

    def __init__(self, d_in: int, d_ff: int):
        super().__init__()
        self.w = nn.Linear(d_in, d_ff * 2)
        self.fc = nn.Linear(d_ff, d_in)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x_glu = self.w(x)
        x, gate = x_glu.chunk(2, dim=-1)
        x = x * F.silu(gate)
        return self.fc(x)


class MTPHead(nn.Module):
    """Multi-task pooling head: routes the final embedding to several heads.

    Each task head maps to the vocab; the router weights the per-task outputs
    by a softmaxed routing vector. ``task_weights`` holds the prior weights.
    """

    def __init__(self, d_model: int, vocab_size: int, num_tasks: int = 3):
        super().__init__()
        self.num_tasks = num_tasks
        self.task_heads = nn.ModuleList(
            [nn.Linear(d_model, vocab_size) for _ in range(num_tasks)]
        )
        self.task_weights = nn.Parameter(torch.ones(num_tasks) / num_tasks)
        self.task_router = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.ReLU(),
            nn.Linear(d_model // 2, num_tasks),
        )

    def forward(
        self, x: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        task_logits = self.task_router(x)
        task_probs = F.softmax(task_logits, dim=-1)

        task_outputs = [head(x) for head in self.task_heads]
        task_outputs = torch.stack(task_outputs, dim=1)

        weighted = (task_probs.unsqueeze(-1) * task_outputs).sum(dim=1)
        return weighted, task_probs


class LoopedTransformerLayer(nn.Module):
    """One self-attention + SwiGLU block, applied ``num_loops`` times."""

    def __init__(
        self,
        d_model: int,
        nhead: int,
        dim_feedforward: int,
        num_loops: int = 3,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.num_loops = num_loops
        self.qk_norm = QKNorm(d_model // nhead)
        self.attention = nn.MultiheadAttention(
            d_model, nhead, batch_first=True, dropout=dropout
        )
        self.norm1 = RMSNorm(d_model)
        self.norm2 = RMSNorm(d_model)
        self.ffn = SwiGLU(d_model, dim_feedforward)
        self.dropout = nn.Dropout(dropout)

    def forward(
        self,
        x: torch.Tensor,
        mask: torch.Tensor | None = None,
        is_causal: bool = False,
    ) -> torch.Tensor:
        for _ in range(self.num_loops):
            x_norm = self.norm1(x)
            attn_out, _ = self.attention(
                x_norm, x_norm, x_norm, attn_mask=mask, is_causal=is_causal
            )
            x = x + self.dropout(attn_out)

            x_norm = self.norm2(x)
            ffn_out = self.ffn(x_norm)
            x = x + self.dropout(ffn_out)

        return x


class LoopedTransformer(nn.Module):
    """Stack of looped Transformer layers."""

    def __init__(
        self,
        d_model: int,
        nhead: int,
        num_layers: int,
        dim_feedforward: int,
        num_loops: int = 3,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.layers = nn.ModuleList(
            [
                LoopedTransformerLayer(
                    d_model, nhead, dim_feedforward, num_loops, dropout
                )
                for _ in range(num_layers)
            ]
        )

    def forward(
        self,
        x: torch.Tensor,
        mask: torch.Tensor | None = None,
        is_causal: bool = False,
    ) -> torch.Tensor:
        for layer in self.layers:
            x = layer(x, mask=mask, is_causal=is_causal)
        return x


class FormulaPolicyNet(nn.Module):
    """Causal policy network over the formula token vocabulary.

    Maps a partial token sequence to a next-token logits distribution, a
    scalar value estimate, and per-task routing weights. The attribute
    contract consumed by the mining engines (unchanged by the rename):

    - ``d_model``, ``vocab``, ``vocab_size``, ``features_list``, ``ops_list``
    - ``token_emb``, ``pos_emb``, ``blocks``, ``ln_f``
    - ``mtp_head`` (``task_heads`` / ``task_router`` / ``task_weights``)
    - ``head_critic``
    - ``forward`` returns ``(logits, value, task_probs)``
    """

    def __init__(self, config: AlphaCoreConfig | None = None):
        super().__init__()
        config = config or AlphaCoreConfig()
        self.d_model = 64
        self.features_list = list(FORMULA_VOCAB.feature_names)
        self.ops_list = list(FORMULA_VOCAB.operator_names)
        self.vocab = list(FORMULA_VOCAB.token_names)
        self.vocab_size = FORMULA_VOCAB.size

        self.token_emb = nn.Embedding(self.vocab_size, self.d_model)
        self.pos_emb = nn.Parameter(
            torch.zeros(1, config.MAX_FORMULA_LEN + 1, self.d_model)
        )

        self.blocks = LoopedTransformer(
            d_model=self.d_model,
            nhead=4,
            num_layers=2,
            dim_feedforward=128,
            num_loops=3,
            dropout=0.1,
        )

        self.ln_f = RMSNorm(self.d_model)
        self.mtp_head = MTPHead(self.d_model, self.vocab_size, num_tasks=3)
        self.head_critic = nn.Linear(self.d_model, 1)

    def forward(
        self, idx: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        batch, seq_len = idx.size()

        hidden = self.token_emb(idx) + self.pos_emb[:, :seq_len, :]

        mask = nn.Transformer.generate_square_subsequent_mask(seq_len).to(idx.device)
        hidden = self.blocks(hidden, mask=mask, is_causal=True)
        hidden = self.ln_f(hidden)

        last = hidden[:, -1, :]
        logits, task_probs = self.mtp_head(last)
        value = self.head_critic(last)

        return logits, value, task_probs

"""Formula operators (torch-native)."""

import torch


@torch.jit.script
def _ts_delay(x: torch.Tensor, d: int) -> torch.Tensor:
    if d == 0:
        return x
    pad = torch.zeros((x.shape[0], d), device=x.device)
    return torch.cat([pad, x[:, :-d]], dim=1)


@torch.jit.script
def _op_gate(condition: torch.Tensor, x: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
    mask = (condition > 0).float()
    return mask * x + (1.0 - mask) * y


@torch.jit.script
def _op_jump(x: torch.Tensor) -> torch.Tensor:
    mean = x.mean(dim=1, keepdim=True)
    std = x.std(dim=1, keepdim=True) + 1e-6
    z = (x - mean) / std
    return torch.relu(z - 3.0)


@torch.jit.script
def _op_decay(x: torch.Tensor) -> torch.Tensor:
    return x + 0.8 * _ts_delay(x, 1) + 0.6 * _ts_delay(x, 2)


@torch.jit.script
def _op_ts_mean(x: torch.Tensor) -> torch.Tensor:
    d1 = _ts_delay(x, 1)
    d2 = _ts_delay(x, 2)
    d3 = _ts_delay(x, 3)
    d4 = _ts_delay(x, 4)
    return (x + d1 + d2 + d3 + d4) / 5.0


@torch.jit.script
def _op_ts_std(x: torch.Tensor) -> torch.Tensor:
    m = _op_ts_mean(x)
    d1 = _ts_delay(x, 1)
    d2 = _ts_delay(x, 2)
    d3 = _ts_delay(x, 3)
    d4 = _ts_delay(x, 4)
    var = (
        (x - m) ** 2 + (d1 - m) ** 2 + (d2 - m) ** 2 + (d3 - m) ** 2 + (d4 - m) ** 2
    ) / 5.0
    return torch.sqrt(var + 1e-12)


@torch.jit.script
def _op_ts_rank(x: torch.Tensor) -> torch.Tensor:
    d1 = _ts_delay(x, 1)
    d2 = _ts_delay(x, 2)
    d3 = _ts_delay(x, 3)
    d4 = _ts_delay(x, 4)
    rank = (
        (x >= x).float()
        + (x >= d1).float()
        + (x >= d2).float()
        + (x >= d3).float()
        + (x >= d4).float()
    ) / 5.0
    return rank


@torch.jit.script
def _op_rank(x: torch.Tensor) -> torch.Tensor:
    return (x - x.mean(dim=1, keepdim=True)) / (x.std(dim=1, keepdim=True) + 1e-6)


@torch.jit.script
def _op_ts_delta(x: torch.Tensor) -> torch.Tensor:
    return x - _ts_delay(x, 1)


@torch.jit.script
def _op_ts_max(x: torch.Tensor) -> torch.Tensor:
    return torch.max(
        x,
        torch.max(
            _ts_delay(x, 1),
            torch.max(_ts_delay(x, 2), torch.max(_ts_delay(x, 3), _ts_delay(x, 4))),
        ),
    )


@torch.jit.script
def _op_ts_min(x: torch.Tensor) -> torch.Tensor:
    return torch.min(
        x,
        torch.min(
            _ts_delay(x, 1),
            torch.min(_ts_delay(x, 2), torch.min(_ts_delay(x, 3), _ts_delay(x, 4))),
        ),
    )


@torch.jit.script
def _op_ts_corr(x: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
    """Per-token Pearson correlation of two series over the time axis.

    Returns a tensor shaped like ``x`` (the scalar correlation per token is
    broadcast across time) — every other operator assumes [tokens, timesteps]
    input, so this must never collapse the time dimension.
    """
    xm = x - x.mean(dim=1, keepdim=True)
    ym = y - y.mean(dim=1, keepdim=True)
    corr = (xm * ym).sum(dim=1) / (xm.norm(p=2, dim=1) * ym.norm(p=2, dim=1) + 1e-8)
    return corr.unsqueeze(1).expand_as(x)


# (name, callable, arity)
OPS_CONFIG: list[tuple[str, object, int]] = [
    ("ADD", lambda x, y: x + y, 2),
    ("SUB", lambda x, y: x - y, 2),
    ("MUL", lambda x, y: x * y, 2),
    ("DIV", lambda x, y: x / (y + 1e-6), 2),
    ("NEG", lambda x: -x, 1),
    ("ABS", torch.abs, 1),
    ("SIGN", torch.sign, 1),
    ("GATE", _op_gate, 3),
    ("JUMP", _op_jump, 1),
    ("DECAY", _op_decay, 1),
    ("DELAY1", lambda x: _ts_delay(x, 1), 1),
    (
        "MAX3",
        lambda x: torch.max(x, torch.max(_ts_delay(x, 1), _ts_delay(x, 2))),
        1,
    ),
    ("MEAN2", lambda x, y: (x + y) / 2.0, 2),
    # V4: Time-series operators
    ("TS_MEAN", _op_ts_mean, 1),
    ("TS_STD", _op_ts_std, 1),
    ("TS_RANK", _op_ts_rank, 1),
    ("TS_CORR", _op_ts_corr, 2),
    ("RANK", _op_rank, 1),
    ("TS_DELTA", _op_ts_delta, 1),
    ("TS_MAX", _op_ts_max, 1),
    ("TS_MIN", _op_ts_min, 1),
]

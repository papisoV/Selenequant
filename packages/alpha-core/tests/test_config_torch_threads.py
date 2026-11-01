"""Thread-cap resolution for the mining engines (R1).

The uncapped torch default (cpu-count, here 16) is both slower in wall-clock
and ~11x more CPU than a cap of 4 on a 256-batch REINFORCE step — the hot loop
is per-sample pure Python, where torch's parallel regions cannot help. These
tests lock the resolution contract; the wall-clock/CPU claim is measured
out-of-band (scripts/_bench_runtime_cap.py).
"""

from __future__ import annotations

import logging

import pytest
import torch

from selene_space_alpha.config import (
    AlphaCoreConfig,
    resolve_torch_num_threads,
)
from selene_space_alpha.mining.engine import AlphaEngine


def test_default_is_four() -> None:
    assert AlphaCoreConfig().TORCH_NUM_THREADS == 4


def test_override_wins() -> None:
    assert AlphaCoreConfig(TORCH_NUM_THREADS=2).TORCH_NUM_THREADS == 2


def test_env_override_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELENE_ALPHA_TORCH_THREADS", "3")
    assert resolve_torch_num_threads() == 3


def test_explicit_arg_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELENE_ALPHA_TORCH_THREADS", "3")
    assert resolve_torch_num_threads(8) == 8


@pytest.mark.parametrize("bad", ["abc", "0", "-3", "1.5", ""])
def test_non_integer_or_below_one_falls_back(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("SELENE_ALPHA_TORCH_THREADS", bad)
    assert resolve_torch_num_threads() == 4


def test_bad_env_warns(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    monkeypatch.setenv("SELENE_ALPHA_TORCH_THREADS", "0")
    with caplog.at_level(logging.WARNING):
        resolve_torch_num_threads()
    assert any("SELENE_ALPHA_TORCH_THREADS" in r.getMessage() for r in caplog.records)


def test_engine_applies_cap_to_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Constructing an engine must move the process-wide torch thread count."""
    monkeypatch.setenv("SELENE_ALPHA_TORCH_THREADS", "2")
    before = torch.get_num_threads()
    assert before != 2, "test precondition: machine must have >2 cores"
    AlphaEngine(config=AlphaCoreConfig(), output_dir=str(tmp_path))
    assert torch.get_num_threads() == 2
    # Restore so later tests in the session are not silently capped.
    torch.set_num_threads(before)

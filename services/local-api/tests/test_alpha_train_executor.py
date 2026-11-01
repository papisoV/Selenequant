"""R2: training runs in a bounded, dedicated executor.

Why it matters: training is CPU-bound and long-running. The event loop's
default executor is shared with I/O-bound callers (outbound gateway), so a
training session could starve unrelated endpoints. These tests lock the pool's
size contract and that the training path actually uses it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.api.v1 import alpha


@pytest.fixture(autouse=True)
def _reset_pool(monkeypatch):
    """Each test gets a fresh pool; never leak a sized-to-env singleton."""
    monkeypatch.setattr(alpha, "_train_executor_instance", None)


def test_pool_sized_by_batch_cap_default(monkeypatch) -> None:
    monkeypatch.delenv("SELENE_ALPHA_BATCH_MAX_CONCURRENCY", raising=False)
    pool = alpha._train_executor()
    assert isinstance(pool, ThreadPoolExecutor)
    assert pool._max_workers == alpha._DEFAULT_BATCH_MAX_CONCURRENCY


def test_pool_sized_by_env(monkeypatch) -> None:
    monkeypatch.setenv("SELENE_ALPHA_BATCH_MAX_CONCURRENCY", "6")
    assert alpha._train_executor()._max_workers == 6


def test_pool_is_singleton(monkeypatch) -> None:
    monkeypatch.delenv("SELENE_ALPHA_BATCH_MAX_CONCURRENCY", raising=False)
    assert alpha._train_executor() is alpha._train_executor()


def test_training_does_not_use_default_executor() -> None:
    """The training submission must name an executor, not pass None."""
    import inspect

    src = inspect.getsource(alpha._run_training)
    assert "run_in_executor(\n                _train_executor()," in src
    assert "_train_executor()" in src

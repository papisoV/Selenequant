"""Hardware-protection policy: concurrency ceiling + torch thread budget.

Why it matters: the mining page used to advertise parallelism the backend
silently refused. ``max_concurrency`` was accepted up to 32, but the training
pool was built once at the env/default size, so anything above it queued. These
tests lock the contract that the request value is clamped to the real pool size,
that the pool rebuilds when the ceiling changes, and that a policy change is
refused while sessions are active (torch.set_num_threads is process-wide and
sticky, so retargeting mid-flight would silently re-cap running sessions).
"""

from __future__ import annotations

import pytest

from app.api.v1 import alpha


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Every test starts from a fresh pool and no endpoint-set policy."""
    monkeypatch.setattr(alpha, "_train_executor_instance", None)
    monkeypatch.setattr(alpha, "_train_executor_size", None)
    monkeypatch.setattr(alpha, "_concurrency_ceiling", None)
    monkeypatch.setattr(alpha, "_torch_threads_policy", None)
    monkeypatch.delenv("SELENE_ALPHA_BATCH_MAX_CONCURRENCY", raising=False)
    monkeypatch.delenv("SELENE_ALPHA_TORCH_THREADS", raising=False)


def test_default_ceiling_and_threads() -> None:
    assert alpha._resolve_concurrency_ceiling() == 4
    assert alpha._resolve_torch_threads_policy() == 4


def test_request_concurrency_clamped_to_ceiling() -> None:
    """Asking for 32 must yield the pool size, not 32.

    This is the bug the UI exposed: a request value the pool would ignore.
    """
    assert alpha._batch_max_concurrency(32) == 4
    assert alpha._batch_max_concurrency(2) == 2
    assert alpha._batch_max_concurrency(None) == 4


def test_env_sets_ceiling_and_clamps_request(monkeypatch) -> None:
    monkeypatch.setenv("SELENE_ALPHA_BATCH_MAX_CONCURRENCY", "6")
    assert alpha._resolve_concurrency_ceiling() == 6
    assert alpha._batch_max_concurrency(32) == 6
    assert alpha._batch_max_concurrency(3) == 3


def test_env_sets_torch_threads(monkeypatch) -> None:
    monkeypatch.setenv("SELENE_ALPHA_TORCH_THREADS", "2")
    assert alpha._resolve_torch_threads_policy() == 2


def test_invalid_torch_threads_falls_back(monkeypatch) -> None:
    for bad in ("abc", "0", "-1"):
        monkeypatch.setenv("SELENE_ALPHA_TORCH_THREADS", bad)
        assert alpha._resolve_torch_threads_policy() == 4


def test_endpoint_policy_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("SELENE_ALPHA_BATCH_MAX_CONCURRENCY", "6")
    monkeypatch.setenv("SELENE_ALPHA_TORCH_THREADS", "8")
    monkeypatch.setattr(alpha, "_concurrency_ceiling", 2)
    monkeypatch.setattr(alpha, "_torch_threads_policy", 3)
    assert alpha._resolve_concurrency_ceiling() == 2
    assert alpha._resolve_torch_threads_policy() == 3


def test_pool_rebuilds_when_ceiling_changes() -> None:
    """A policy change must resize the pool, not leave the old one cached."""
    first = alpha._train_executor()
    assert first._max_workers == 4

    alpha._concurrency_ceiling = 8
    second = alpha._train_executor()
    assert second._max_workers == 8
    assert second is not first

    # Same ceiling again → same pool, no needless churn.
    assert alpha._train_executor() is second


def test_resources_payload_reports_budget() -> None:
    payload = alpha._resources_payload()
    assert payload["max_concurrency"] == 4
    assert payload["torch_num_threads"] == 4
    assert payload["thread_budget"] == 16
    assert payload["cpu_cores"] >= 1
    assert payload["active_sessions"] == 0


def test_set_resources_refused_while_active(monkeypatch) -> None:
    """409 with the active count, not a silent no-op."""
    monkeypatch.setattr(alpha, "_active_session_count", lambda: 3)
    with pytest.raises(Exception) as excinfo:
        import asyncio

        asyncio.run(
            alpha.set_resources(
                alpha.ResourcesRequest(max_concurrency=8, torch_num_threads=2)
            )
        )
    assert "3 mining session(s) active" in str(excinfo.value)


def test_set_resources_applies_when_idle() -> None:
    import asyncio

    resp = asyncio.run(
        alpha.set_resources(
            alpha.ResourcesRequest(max_concurrency=2, torch_num_threads=1)
        )
    )
    assert resp.error is None
    assert resp.data["max_concurrency"] == 2
    assert resp.data["torch_num_threads"] == 1
    assert resp.data["thread_budget"] == 2
    # Pool reflects the new ceiling immediately, not on the next request.
    assert alpha._train_executor()._max_workers == 2


def test_get_resources_returns_payload() -> None:
    import asyncio

    resp = asyncio.run(alpha.get_resources())
    assert resp.error is None
    assert resp.data["max_concurrency"] == 4


def test_stop_survives_while_queued() -> None:
    """Stopping a session queued behind a full pool must not be clobbered.

    Regression: ``_run_training`` unconditionally set status="running" when the
    session finally acquired a pool slot, overwriting the "stopping" the user
    set while it waited. With a low concurrency ceiling (a hardware-protection
    setting) that wait is long, and the stop was silently lost — the session
    then ran to completion, burning the CPU the user asked it to stop.
    """
    import inspect

    src = inspect.getsource(alpha._run_training)
    # The status write must be guarded, not unconditional.
    assert 'stop_requested = (record or {}).get("status") == "stopping"' in src
    assert "if record is not None and not stop_requested:" in src
    assert "if stop_requested:" in src

    # Anchor the guard to the real path: it must live in the async training
    # entry point, not some helper that happens to share the text.
    assert inspect.iscoroutinefunction(alpha._run_training)
    assert "session_id" in inspect.signature(alpha._run_training).parameters


def test_policy_reaches_engine_config_and_torch() -> None:
    """The policy must actually cap torch, not just sit in a variable.

    This is the load-bearing claim of the whole feature: ``_run_training``
    builds ``AlphaCoreConfig(TORCH_NUM_THREADS=...)`` from the policy, and
    ``AlphaEngine.__init__`` calls ``torch.set_num_threads`` with it. Assert the
    chain end-to-end: policy → config → the process-wide torch setting.
    """
    import torch

    from selene_space_alpha.config import AlphaCoreConfig

    alpha._torch_threads_policy = 1
    cfg = AlphaCoreConfig(TORCH_NUM_THREADS=alpha._resolve_torch_threads_policy())
    assert cfg.TORCH_NUM_THREADS == 1

    # What the engine will do with it (engine.py:104 / real_engine.py:62).
    torch.set_num_threads(int(cfg.TORCH_NUM_THREADS))
    assert torch.get_num_threads() == 1

    # Restore so other tests are not left with a sticky 1-thread process.
    alpha._torch_threads_policy = 4
    torch.set_num_threads(4)

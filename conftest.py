"""Repo-wide test isolation for globally-singleton, disk-persisted state.

``KillSwitch`` is a process-wide singleton that persists to
``~/.selenespace/kill_switch.json`` whenever it is constructed with no
path.  Any test that does that — and any later test in the same process,
because the singleton keeps the first instance — reads and writes the
developer's real kill-switch state:

* a CRITICAL risk rule in a unit test arms the switch and leaves every
  subsequent order rejected with 403 until someone notices;
* ``deactivate()`` in a test disarms a switch the user armed on purpose,
  which is the more dangerous direction: a safety control silently
  removed by ``pytest``.

Both have happened (see the merge log 2026-10-22).  Redirect the
default path at every test and reset the singleton on both sides, so no
test can reach the real file.
"""

from __future__ import annotations


import pytest


@pytest.fixture(autouse=True)
def _isolated_runner_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
):
    """Redirect StrategyRunner's deployments.json away from the user's disk.

    Booting the real app lifespan constructs a ``StrategyRunner`` whose state
    defaults to ``~/.selenespace/runner/deployments.json``.  A test that deploys
    through the lifespan (e.g. ``test_astock_deploy_e2e``) therefore writes a
    real deployment record onto the user's disk — and a *later* test in the
    same process restores it, reads "Deployment dep-e2e-astock-smoke already
    running", and fails (2026-10-22).  Same shape as the kill-switch leak:
    test isolation must never write to real user state.
    """
    runner_dir = tmp_path_factory.mktemp("runner_state")
    monkeypatch.setenv("SELENE_RUNNER_STATE_DIR", str(runner_dir))
    yield


@pytest.fixture(autouse=True)
def _isolated_kill_switch_state(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    """Point KillSwitch's default state path at a temp dir for this test."""
    try:
        import selene_space_risk.kill_switch as ks_module
        from selene_space_risk.kill_switch import KillSwitch
    except ImportError:  # package not importable here — nothing to isolate
        yield
        return

    state_dir = tmp_path_factory.mktemp("kill_switch")
    monkeypatch.setattr(
        ks_module, "_DEFAULT_STATE_PATH", state_dir / "kill_switch.json"
    )
    KillSwitch._reset_singleton()
    try:
        yield
    finally:
        KillSwitch._reset_singleton()

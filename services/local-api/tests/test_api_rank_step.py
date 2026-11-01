"""US-001: /train/{sid}/status passthrough of rank_step + reward_std.

The engine's history contract now carries ``rank_step`` (sparse, aligned
with ``stable_rank``) and ``reward_std`` (dense, per-step). This locks the
API surface: sessions seed both keys, live write-through appends them in
lockstep, and status responses expose them verbatim.
"""

from __future__ import annotations

import pytest

from app.api.v1 import alpha


@pytest.fixture(autouse=True)
def _clean_sessions(monkeypatch):
    monkeypatch.setattr(alpha, "_sessions", {})
    yield


def _mk_session(sid: str, status: str = "running") -> None:
    with alpha._sessions_lock:
        alpha._sessions[sid] = {
            "status": status,
            "step": 0,
            "best_score": -float("inf"),
            "train_steps": 100,
            "config": {},
            "started_at": 100.0,
            "history": {
                "step": [],
                "avg_reward": [],
                "best_score": [],
                "stable_rank": [],
                "rank_step": [],
                "reward_std": [],
            },
            "logs": [],
        }


@pytest.mark.asyncio
async def test_status_exposes_new_history_keys() -> None:
    _mk_session("alpha_rank_status")
    with alpha._sessions_lock:
        s = alpha._sessions["alpha_rank_status"]
        s["history"]["step"] = [0, 1, 2]
        s["history"]["avg_reward"] = [0.1, 0.2, 0.3]
        s["history"]["reward_std"] = [0.5, 0.4, 0.3]
        s["history"]["stable_rank"] = [14.2]
        s["history"]["rank_step"] = [0]

    resp = await alpha.get_training_status("alpha_rank_status")
    hist = resp.data["history"]
    assert hist["reward_std"] == [0.5, 0.4, 0.3]
    assert hist["rank_step"] == [0]
    assert list(zip(hist["rank_step"], hist["stable_rank"])) == [(0, 14.2)]


@pytest.mark.asyncio
async def test_on_step_write_through_appends_new_keys() -> None:
    """The _run_training on_step callback mirrors engine appends: rank_step
    only with stable_rank (same branch), reward_std every step from the
    engine tail. Snapshots are taken mid-run — completion overwrites session
    history with ``dict(engine.training_history)``, so asserting only the
    final state would pass even without any write-through."""

    snapshots: list[dict] = []

    class _FakeEngineWithNewKeys:
        def __init__(self, **kwargs):
            self.best_score = 0.42
            self.best_formula = [0, 6, 3, 1]
            self.training_history = {
                "step": [],
                "avg_reward": [],
                "best_score": [],
                "stable_rank": [],
                "rank_step": [],
                "reward_std": [],
            }

        def train(
            self,
            on_step=None,
            start_step=0,
            checkpoint_path=None,
            checkpoint_interval=10,
        ):
            # Mirror the engine's append order: reward_std lands in
            # training_history before on_step fires.
            self.training_history["step"].append(0)
            self.training_history["reward_std"].append(0.61)
            self.training_history["stable_rank"].append(14.2)
            self.training_history["rank_step"].append(0)
            on_step(0, 0.1, self.best_score, 14.2, None)
            with alpha._sessions_lock:
                snapshots.append(
                    {
                        k: list(v)
                        for k, v in alpha._sessions["alpha_rank_wt"]["history"].items()
                    }
                )

            self.training_history["step"].append(1)
            self.training_history["reward_std"].append(0.58)
            on_step(1, 0.12, self.best_score, None, None)
            with alpha._sessions_lock:
                snapshots.append(
                    {
                        k: list(v)
                        for k, v in alpha._sessions["alpha_rank_wt"]["history"].items()
                    }
                )

        def save_checkpoint(self, path, step, extra=None):
            pass

    class _FakeLoader:
        def __init__(self, config=None):
            self.config = config

        def _create_synthetic_data(self, n_tokens=300):
            pass

        def load_from_sqlite(self, **kwargs):
            raise FileNotFoundError("no data (stub)")

    monkeypatch_engine = pytest.MonkeyPatch()
    monkeypatch_engine.setattr(
        "selene_space_alpha.mining.AlphaEngine", _FakeEngineWithNewKeys
    )
    monkeypatch_engine.setattr(
        "selene_space_alpha.data_loader.CryptoDataLoader", _FakeLoader
    )
    try:
        _mk_session("alpha_rank_wt", status="starting")
        req = alpha.TrainRequest(train_steps=2, batch_size=8, use_synthetic_data=True)
        await alpha._run_training("alpha_rank_wt", req)
    finally:
        monkeypatch_engine.undo()

    # Mid-run snapshot after step 0 (LoRD branch fired):
    after0 = snapshots[0]
    assert after0["rank_step"] == [0]
    assert after0["stable_rank"] == [14.2]
    assert after0["reward_std"] == [0.61]
    # Mid-run snapshot after step 1 (no rank sample — sparse stays sparse):
    after1 = snapshots[1]
    assert after1["rank_step"] == [0]
    assert after1["reward_std"] == [0.61, 0.58]
    assert after1["step"] == [0, 1]


@pytest.mark.asyncio
async def test_start_training_seeds_new_keys(monkeypatch) -> None:
    """POST /train's session seed carries the new keys, so a status poll
    before the first step already exposes them (frontend projection needs
    no guessing)."""

    async def _noop_run(*args, **kwargs):
        pass

    monkeypatch.setattr(alpha, "_run_training", _noop_run)
    req = alpha.TrainRequest(train_steps=10, batch_size=8, use_synthetic_data=True)
    resp = await alpha.start_training(req)
    sid = resp.data["session_id"]

    status = await alpha.get_training_status(sid)
    assert set(status.data["history"]) >= {
        "step",
        "avg_reward",
        "best_score",
        "stable_rank",
        "rank_step",
        "reward_std",
    }

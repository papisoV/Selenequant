"""US-001: POST /alpha/train/batch — thin batch orchestrator over the existing
single-session training path.

Contract under test:
  * one session per (symbol, interval) pair in the cartesian product
  * a concurrency cap (``SELENE_ALPHA_BATCH_MAX_CONCURRENCY`` / per-request
    override) bounds how many sessions are in flight at once
  * a session that dies takes itself down, not the batch
  * empty symbol/interval lists are 422, unknown batch ids are 404
  * the pre-existing single-session POST /train is untouched

Every case stubs ``_run_training`` — these lock the orchestrator's own
behaviour (fan-out geometry, cap, rollup, isolation), not the engine's.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from pydantic import ValidationError

from app.api.v1 import alpha


@pytest.fixture(autouse=True)
def _clean_registries(monkeypatch):
    monkeypatch.setattr(alpha, "_sessions", {})
    monkeypatch.setattr(alpha, "_batches", {})
    yield


async def _drain(batch_id: str, *, timeout: float = 5.0) -> None:
    """Wait for the background batch task to publish its frozen status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with alpha._sessions_lock:
            batch = alpha._batches.get(batch_id)
        if batch is not None and "finished_at" in batch:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"batch {batch_id} never finished within {timeout}s")


async def _complete(session_id: str) -> None:
    with alpha._sessions_lock:
        alpha._sessions[session_id]["status"] = "completed"


@pytest.mark.asyncio
async def test_batch_start_fans_out_cartesian_product(monkeypatch) -> None:
    """2 symbols x 3 intervals -> 6 distinct sessions, ids unique."""

    async def _ok(session_id, session_request):
        await _complete(session_id)

    monkeypatch.setattr(alpha, "_run_training", _ok)
    resp = await alpha.start_batch_training(
        alpha.BatchTrainRequest(
            symbols=["BTC/USDT", "ETH/USDT"],
            intervals=["1h", "4h", "1d"],
            train_steps=5,
        )
    )

    data = resp.data
    assert data["status"] == "running"
    assert data["max_concurrency"] == alpha._DEFAULT_BATCH_MAX_CONCURRENCY
    session_ids = data["session_ids"]
    assert len(session_ids) == 6
    assert len(set(session_ids)) == 6


@pytest.mark.asyncio
async def test_session_ids_do_not_collide_within_one_tick(monkeypatch) -> None:
    """Session ids seed in a single tick, and the id is also the checkpoint
    *directory* name — second-precision ids alone would clobber each other."""

    async def _ok(session_id, session_request):
        await _complete(session_id)

    monkeypatch.setattr(alpha, "_run_training", _ok)
    resp = await alpha.start_batch_training(
        alpha.BatchTrainRequest(
            symbols=["A/USDT", "B/USDT"], intervals=["1h", "4h"], train_steps=5
        )
    )
    ids = resp.data["session_ids"]
    assert len(set(ids)) == len(ids) == 4
    assert not any("alpha_" not in sid for sid in ids)


@pytest.mark.asyncio
async def test_batch_status_passes_each_session_through(monkeypatch) -> None:
    """The status endpoint reports the (symbol, interval) pairing per session
    along with step/train_steps/best_score — the frontend needs the pairing to
    attribute a finished session back to its market."""

    async def _hold(session_id, session_request):
        return None

    monkeypatch.setattr(alpha, "_run_training", _hold)
    resp = await alpha.start_batch_training(
        alpha.BatchTrainRequest(
            symbols=["BTC/USDT", "ETH/USDT"], intervals=["1h", "4h"], train_steps=7
        )
    )
    status = await alpha.get_batch_status(resp.data["batch_id"])

    data = status.data
    assert data["batch_id"] == resp.data["batch_id"]
    assert data["session_count"] == 4
    pairs = {(s["symbol"], s["interval"]) for s in data["sessions"]}
    assert pairs == {
        ("BTC/USDT", "1h"),
        ("BTC/USDT", "4h"),
        ("ETH/USDT", "1h"),
        ("ETH/USDT", "4h"),
    }
    for session in data["sessions"]:
        assert session["train_steps"] == 7
        assert session["step"] == 0
        # -inf is normalized away; JSON cannot carry it.
        assert session["best_score"] is None


@pytest.mark.asyncio
async def test_batch_sessions_carry_the_per_session_symbol(monkeypatch) -> None:
    """The batch-only list fields must not leak into the session config — each
    session's config is a plain TrainRequest with a single symbol."""

    async def _hold(session_id, session_request):
        return None

    monkeypatch.setattr(alpha, "_run_training", _hold)
    await alpha.start_batch_training(
        alpha.BatchTrainRequest(
            symbols=["BTC/USDT", "ETH/USDT"],
            intervals=["4h"],
            train_steps=3,
            use_synthetic_data=True,
        )
    )
    with alpha._sessions_lock:
        configs = [dict(s["config"]) for s in alpha._sessions.values()]
    assert len(configs) == 2
    for config in configs:
        assert "symbols" not in config
        assert "intervals" not in config
        assert "max_concurrency" not in config
        assert config["interval"] == "4h"
        assert config["use_synthetic_data"] is True


@pytest.mark.asyncio
async def test_concurrency_cap_bounds_sessions_in_flight(monkeypatch) -> None:
    """With 6 sessions and a cap of 2, exactly 2 run at once — the cap is
    enforced, and it is a cap rather than a serialization."""
    inflight = 0
    peak = 0

    async def _slow(session_id, session_request):
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        try:
            await asyncio.sleep(0.02)
        finally:
            inflight -= 1
        await _complete(session_id)

    monkeypatch.setattr(alpha, "_run_training", _slow)
    resp = await alpha.start_batch_training(
        alpha.BatchTrainRequest(
            symbols=["A/USDT", "B/USDT", "C/USDT", "D/USDT", "E/USDT", "F/USDT"],
            intervals=["1h"],
            train_steps=2,
            max_concurrency=2,
        )
    )
    assert resp.data["max_concurrency"] == 2
    await _drain(resp.data["batch_id"])

    assert peak == 2
    status = await alpha.get_batch_status(resp.data["batch_id"])
    assert status.data["status"] == "completed"
    assert {s["status"] for s in status.data["sessions"]} == {"completed"}


def test_concurrency_cap_reads_env_when_request_omits_it(monkeypatch) -> None:
    monkeypatch.setenv("SELENE_ALPHA_BATCH_MAX_CONCURRENCY", "7")
    assert alpha._batch_max_concurrency(None) == 7
    monkeypatch.setenv("SELENE_ALPHA_BATCH_MAX_CONCURRENCY", "not-a-number")
    assert alpha._batch_max_concurrency(None) == alpha._DEFAULT_BATCH_MAX_CONCURRENCY
    monkeypatch.setenv("SELENE_ALPHA_BATCH_MAX_CONCURRENCY", "0")
    assert alpha._batch_max_concurrency(None) == 1


@pytest.mark.asyncio
async def test_single_session_failure_does_not_sink_the_batch(monkeypatch) -> None:
    """One session blowing up leaves the others untouched; the batch rolls up
    to ``partial`` rather than being reported as a clean success."""

    async def _flaky(session_id, session_request):
        if session_request.symbol == "BAD/USDT":
            raise RuntimeError("engine exploded")
        await _complete(session_id)

    monkeypatch.setattr(alpha, "_run_training", _flaky)
    resp = await alpha.start_batch_training(
        alpha.BatchTrainRequest(
            symbols=["OK/USDT", "BAD/USDT"], intervals=["1h"], train_steps=2
        )
    )
    await _drain(resp.data["batch_id"])

    status = await alpha.get_batch_status(resp.data["batch_id"])
    by_symbol = {s["symbol"]: s for s in status.data["sessions"]}
    assert by_symbol["OK/USDT"]["status"] == "completed"
    assert by_symbol["BAD/USDT"]["status"] == "failed"
    assert "engine exploded" in by_symbol["BAD/USDT"]["error"]
    assert status.data["status"] == "partial"


@pytest.mark.asyncio
async def test_all_sessions_failed_reports_failed(monkeypatch) -> None:
    async def _boom(session_id, session_request):
        raise RuntimeError("no market data")

    monkeypatch.setattr(alpha, "_run_training", _boom)
    resp = await alpha.start_batch_training(
        alpha.BatchTrainRequest(symbols=["A/USDT"], intervals=["1h"], train_steps=2)
    )
    await _drain(resp.data["batch_id"])
    status = await alpha.get_batch_status(resp.data["batch_id"])
    assert status.data["status"] == "failed"


@pytest.mark.asyncio
async def test_unknown_batch_id_is_404() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await alpha.get_batch_status("batch_does_not_exist")
    assert exc.value.status_code == 404


def test_empty_symbols_or_intervals_are_rejected() -> None:
    """An empty selection must fail loudly rather than returning a shell
    success with zero sessions."""
    with pytest.raises(ValidationError):
        alpha.BatchTrainRequest(symbols=[], intervals=["1h"])
    with pytest.raises(ValidationError):
        alpha.BatchTrainRequest(symbols=["BTC/USDT"], intervals=[])


@pytest.mark.asyncio
async def test_single_train_endpoint_is_unchanged(monkeypatch) -> None:
    """Regression: POST /train still seeds one session with the same id shape,
    the same history keys, and the same response envelope."""
    started: list[tuple[str, object]] = []

    async def _capture(session_id, session_request):
        started.append((session_id, session_request))

    monkeypatch.setattr(alpha, "_run_training", _capture)
    request = alpha.TrainRequest(train_steps=10, symbol="BTC/USDT", interval="1h")
    resp = await alpha.start_training(request)

    assert resp.error is None
    assert resp.data["status"] == "starting"
    session_id = resp.data["session_id"]
    assert session_id.startswith("alpha_")
    assert "_" not in session_id[len("alpha_") :]

    with alpha._sessions_lock:
        record = alpha._sessions[session_id]
    assert record["status"] == "starting"
    assert record["config"]["symbol"] == "BTC/USDT"
    assert set(record["history"]) == {
        "step",
        "avg_reward",
        "best_score",
        "stable_rank",
        "rank_step",
        "reward_std",
    }
    # The background task only starts once the loop gets a turn.
    await asyncio.sleep(0)
    assert started == [(session_id, request)]

    status = await alpha.get_training_status(session_id)
    assert status.data["status"] == "starting"

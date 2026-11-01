"""Tests for the long-running task liveness probe.

The probe's whole job is to tell "stopped" from "never needed to run", so the
cases below pin both directions: a loop that ends must be reported, and every
legitimate end state must stay silent.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.task_liveness import (
    register_task_liveness_probe,
    task_liveness_probe,
    task_liveness_verdict,
)
from selene_space_core.diagnostics.health import HealthRegistry


async def _forever() -> None:
    await asyncio.sleep(3600)


async def _return_now() -> None:
    return None


async def _raise_now() -> None:
    raise RuntimeError("boom")


async def _settle() -> None:
    """Let the scheduled tasks reach a terminal state."""
    await asyncio.sleep(0)


def _state(**tasks: object) -> dict:
    return dict(tasks)


@pytest.mark.asyncio
async def test_running_loop_is_healthy():
    task = asyncio.create_task(_forever(), name="mission-worker")
    try:
        await _settle()
        result = task_liveness_verdict(_state(_mission_worker_task=task))
    finally:
        task.cancel()

    assert result.status == "healthy"
    assert result.detail == {}


@pytest.mark.asyncio
async def test_critical_loop_that_returned_is_unhealthy():
    task = asyncio.create_task(_return_now(), name="kline-gap-sweeper")
    await task
    await _settle()

    result = task_liveness_verdict(
        _state(_kline_sweep_task=task, kline_manager=object())
    )

    assert result.status == "unhealthy"
    assert [row["name"] for row in result.detail["stopped"]] == ["kline-gap-sweeper"]
    assert result.detail["stopped"][0]["state"] == "returned"


@pytest.mark.asyncio
async def test_critical_loop_that_raised_is_unhealthy_with_the_error():
    task = asyncio.create_task(_raise_now(), name="mission-worker")
    with pytest.raises(RuntimeError):
        await task
    await _settle()

    result = task_liveness_verdict(_state(_mission_worker_task=task))

    assert result.status == "unhealthy"
    row = result.detail["stopped"][0]
    assert row["state"] == "failed"
    assert "RuntimeError: boom" in row["error"]


@pytest.mark.asyncio
async def test_oneshot_that_returned_is_healthy():
    task = asyncio.create_task(_return_now(), name="kline-startup-sync")
    await task
    await _settle()

    result = task_liveness_verdict(_state(_kline_sync_task=task))

    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_oneshot_that_raised_is_degraded_not_unhealthy():
    task = asyncio.create_task(_raise_now(), name="ashare-style-warmup")
    with pytest.raises(RuntimeError):
        await task
    await _settle()

    result = task_liveness_verdict(_state(_ashare_warmup_task=task))

    assert result.status == "degraded"
    assert result.detail["failed"][0]["name"] == "ashare-style-warmup"


@pytest.mark.asyncio
async def test_cancelled_task_is_not_reported():
    task = asyncio.create_task(_forever(), name="mission-worker")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    result = task_liveness_verdict(_state(_mission_worker_task=task))

    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_absent_and_none_tasks_are_skipped():
    result = task_liveness_verdict(_state(_options_poll_task=None))

    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_a_non_task_value_does_not_crash_the_probe():
    """A bare coroutine or other handle in state must not raise.

    The probe runs inside ``HealthRegistry._run_one``, which turns an exception
    into ``unhealthy``. Raising here would report a healthy app as unserviceable
    on account of a state value the probe cannot interpret.
    """
    result = task_liveness_verdict(_state(_mission_worker_task=object()))

    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_daily_sync_idle_return_is_healthy_when_no_symbol_is_reachable():
    """The loop logs "loop idle" and returns when nothing is reachable."""
    task = asyncio.create_task(_return_now(), name="astock-daily-sync")
    await task
    await _settle()

    result = task_liveness_verdict(
        _state(_astock_daily_sync_task=task, _astock_daily_sync_expected=False)
    )

    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_daily_sync_return_is_unhealthy_when_symbols_are_reachable():
    task = asyncio.create_task(_return_now(), name="astock-daily-sync")
    await task
    await _settle()

    result = task_liveness_verdict(
        _state(_astock_daily_sync_task=task, _astock_daily_sync_expected=True)
    )

    assert result.status == "unhealthy"


@pytest.mark.asyncio
async def test_options_poller_return_is_healthy_without_a_fetcher():
    task = asyncio.create_task(_return_now(), name="options-poller")
    await task
    await _settle()

    result = task_liveness_verdict(
        _state(_options_poll_task=task, market_svc=object(), options_fetcher=None)
    )

    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_options_poller_return_is_unhealthy_with_its_dependencies():
    task = asyncio.create_task(_return_now(), name="options-poller")
    await task
    await _settle()

    result = task_liveness_verdict(
        _state(_options_poll_task=task, market_svc=object(), options_fetcher=object())
    )

    assert result.status == "unhealthy"


@pytest.mark.asyncio
async def test_sweeper_return_is_healthy_without_a_kline_manager():
    task = asyncio.create_task(_return_now(), name="kline-gap-sweeper")
    await task
    await _settle()

    result = task_liveness_verdict(_state(_kline_sweep_task=task, kline_manager=None))

    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_daily_all_return_is_healthy_when_the_manager_has_no_store():
    """The daily-all loop guards on the store, not just the manager.

    Without a DB it logs "has no DB" and returns — a legitimate end state the
    sweeper does not share, so the two must not be folded together.
    """
    task = asyncio.create_task(_return_now(), name="astock-daily-all")
    await task
    await _settle()

    manager = SimpleNamespace(_db=None)
    result = task_liveness_verdict(
        _state(_astock_daily_all_task=task, kline_manager=manager)
    )

    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_daily_all_return_is_unhealthy_when_the_store_exists():
    task = asyncio.create_task(_return_now(), name="astock-daily-all")
    await task
    await _settle()

    manager = SimpleNamespace(_db=object())
    result = task_liveness_verdict(
        _state(_astock_daily_all_task=task, kline_manager=manager)
    )

    assert result.status == "unhealthy"
    assert [row["name"] for row in result.detail["stopped"]] == ["astock-daily-all"]


@pytest.mark.asyncio
async def test_news_loops_are_matched_positionally_and_are_critical():
    collector = asyncio.create_task(_return_now(), name="news-collector")
    tagger = asyncio.create_task(_forever(), name="news-tagger")
    detector = asyncio.create_task(_forever(), name="news-detector")
    await collector
    await _settle()
    try:
        result = task_liveness_verdict(
            _state(_news_ingest_tasks=[collector, tagger, detector])
        )
    finally:
        tagger.cancel()
        detector.cancel()

    assert result.status == "unhealthy"
    assert [row["name"] for row in result.detail["stopped"]] == ["news-collector"]


@pytest.mark.asyncio
async def test_reported_name_is_the_task_name_not_the_state_key():
    """Triage reads ``detail.stopped[].name``.

    Every other test names the task exactly as its state key, so a probe that
    ignored ``task.get_name()`` and always echoed the key would still pass --
    the two are indistinguishable until they disagree. Here they disagree.
    """
    task = asyncio.create_task(_return_now(), name="mission-worker-named-in-lifespan")
    await task
    await _settle()

    result = task_liveness_verdict(_state(_mission_worker_task=task))

    assert result.status == "unhealthy"
    assert [row["name"] for row in result.detail["stopped"]] == [
        "mission-worker-named-in-lifespan"
    ]


@pytest.mark.asyncio
async def test_probe_logs_only_on_transition(caplog):
    halt = asyncio.Event()

    async def _until_halted() -> None:
        await halt.wait()

    task = asyncio.create_task(_until_halted(), name="mission-worker")
    state = _state(_mission_worker_task=task)
    probe = task_liveness_probe(state)
    try:
        await _settle()
        first = await probe()
        halt.set()
        await task
        second = await probe()
        third = await probe()
    finally:
        if not task.done():
            task.cancel()

    assert first.status == "healthy"
    assert second.status == "unhealthy"
    assert third.status == "unhealthy"
    messages = [r.message for r in caplog.records if "task liveness" in r.message]
    assert len(messages) == 1, messages


@pytest.mark.asyncio
async def test_registration_exposes_the_component_on_the_registry():
    task = asyncio.create_task(_forever(), name="mission-worker")
    registry = HealthRegistry()
    try:
        await _settle()
        register_task_liveness_probe(registry, _state(_mission_worker_task=task))
        results = await registry.run_all()
    finally:
        task.cancel()

    assert [r.name for r in results] == ["task_liveness"]
    assert results[0].status == "healthy"

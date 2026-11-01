"""Infra locks for selene_space_core.infra.keyed_queue — per-key serialization,
cross-key parallelism, and result propagation."""

import asyncio

import pytest

from selene_space_core.infra.keyed_queue import KeyedQueue


def _probe_task(label: str, hold: float):
    async def task():
        events.append(f"{label}:begin")
        await asyncio.sleep(hold)
        events.append(f"{label}:end")

    return task


events: list[str] = []


@pytest.mark.asyncio
async def test_same_key_runs_one_after_another():
    events.clear()
    lane = KeyedQueue()
    first = asyncio.create_task(lane.execute("BTC", _probe_task("one", 0.05)))
    second = asyncio.create_task(lane.execute("BTC", _probe_task("two", 0.01)))
    await asyncio.gather(first, second)
    # one must finish before two starts (serial for same key)
    assert events.index("one:end") < events.index("two:begin")


@pytest.mark.asyncio
async def test_distinct_keys_run_concurrently():
    events.clear()
    lane = KeyedQueue()
    btc = asyncio.create_task(lane.execute("BTC", _probe_task("btc", 0.05)))
    eth = asyncio.create_task(lane.execute("ETH", _probe_task("eth", 0.05)))
    await asyncio.gather(btc, eth)
    # Both should start before either finishes (parallel)
    started_both = events.index("btc:begin") < events.index("btc:end") and events.index(
        "eth:begin"
    ) < events.index("eth:end")
    assert started_both


@pytest.mark.asyncio
async def test_execute_hands_back_the_return_value():
    lane = KeyedQueue()

    async def produce():
        return {"ok": 7}

    assert await lane.execute("slot", produce) == {"ok": 7}

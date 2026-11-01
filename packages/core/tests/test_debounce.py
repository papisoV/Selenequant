"""Infra locks for selene_space_core.infra.debounce — trailing-edge firing,
timer reset on newer calls, and explicit cancellation."""

import asyncio

import pytest

from selene_space_core.infra.debounce import Debouncer

QUIET = 0.05


@pytest.mark.asyncio
async def test_single_call_lands_after_window():
    landed = []

    async def sink(value=""):
        landed.append(value or "fired")

    debounced = Debouncer(sink, window_seconds=QUIET)
    await debounced.call()
    await asyncio.sleep(0.1)
    assert len(landed) == 1


@pytest.mark.asyncio
async def test_newer_call_supersedes_pending_one():
    landed = []

    async def sink(value):
        landed.append(value)

    debounced = Debouncer(sink, window_seconds=0.1)
    await debounced.call("v1")
    await asyncio.sleep(0.05)  # inside the window
    await debounced.call("v2")
    await asyncio.sleep(0.15)
    # Only the newest payload should land
    assert landed == ["v2"]


@pytest.mark.asyncio
async def test_cancel_swallows_pending_fire():
    landed = []

    async def sink():
        landed.append(True)

    debounced = Debouncer(sink, window_seconds=QUIET)
    await debounced.call()
    debounced.cancel()
    await asyncio.sleep(0.1)
    assert landed == []

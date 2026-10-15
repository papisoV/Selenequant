"""ReconnectManager: backoff growth, resubscription, auth abort, restart caps."""

from __future__ import annotations

import asyncio

import pytest

from selene_space_exchange.base.reconnect import ReconnectManager
from selene_space_exchange.base.ws_client import BaseWSClient
from selene_space_exchange.types import ReconnectConfig, WSClientConfig


class ScriptedWSClient(BaseWSClient):
    """WS client whose connect() replays a scripted list of outcomes."""

    def __init__(self, *, connect_outcomes: list[Exception | None] | None = None) -> None:
        super().__init__(WSClientConfig(url="wss://scripted.invalid/feed", exchange="test"))
        self._outcomes = list(connect_outcomes or [None])
        self._cursor = 0
        self.connect_count = 0
        self.subscribe_calls: list[list[str]] = []

    def _next_outcome(self) -> Exception | None:
        if self._cursor < len(self._outcomes):
            outcome = self._outcomes[self._cursor]
            self._cursor += 1
            return outcome
        return self._outcomes[-1]

    async def connect(self) -> None:
        self.connect_count += 1
        outcome = self._next_outcome()
        if outcome is not None:
            raise outcome
        await self._dispatch_connect()

    async def subscribe(self, streams: list[str]) -> None:
        self._subscriptions.update(streams)
        self.subscribe_calls.append(streams)

    async def unsubscribe(self, streams: list[str]) -> None:
        self._subscriptions -= set(streams)

    async def close(self) -> None:
        await self._dispatch_disconnect()


@pytest.mark.asyncio
async def test_clean_connect_leaves_counter_at_zero() -> None:
    client = ScriptedWSClient(connect_outcomes=[None])

    async def immediate_stop() -> None:
        await mgr.stop()

    mgr = ReconnectManager(client, ReconnectConfig(initial_delay_s=0.01, max_delay_s=0.05))
    mgr._listen = immediate_stop
    await mgr.run()

    assert (mgr.attempt, client.connect_count) == (0, 1)


@pytest.mark.asyncio
async def test_delay_grows_with_each_failure() -> None:
    config = ReconnectConfig(initial_delay_s=1.0, max_delay_s=30.0, factor=2.0, jitter=0.0)
    mgr = ReconnectManager(ScriptedWSClient(), config)

    ladder = []
    for attempt in (1, 2, 3):
        mgr._attempt = attempt
        ladder.append(mgr._compute_delay())

    assert ladder[0] < ladder[1] < ladder[2]


@pytest.mark.asyncio
async def test_jitter_stays_inside_twenty_percent_band() -> None:
    config = ReconnectConfig(initial_delay_s=10.0, max_delay_s=30.0, factor=2.0, jitter=0.2)
    mgr = ReconnectManager(ScriptedWSClient(), config)
    mgr._attempt = 1

    samples = [mgr._compute_delay() for _ in range(200)]
    # Base for attempt 1 is initial_delay_s; jitter widens to [8.0, 12.0].
    assert all(7.9 <= d <= 12.1 for d in samples), f"range: {min(samples)}..{max(samples)}"


@pytest.mark.asyncio
async def test_streams_restored_after_drop_and_rejoin() -> None:
    client = ScriptedWSClient(connect_outcomes=[None, ConnectionError("fail"), None])
    await client.subscribe(["btcusdt@ticker", "ethusdt@trade"])

    listen_invocations = 0

    async def listen() -> None:
        nonlocal listen_invocations
        listen_invocations += 1
        if listen_invocations == 1:
            raise ConnectionError("disconnect")
        await mgr.stop()

    mgr = ReconnectManager(client, ReconnectConfig(initial_delay_s=0.01, max_delay_s=0.05), listen=listen)
    await mgr.run()

    assert len(client.subscribe_calls) >= 2


@pytest.mark.asyncio
async def test_auth_failure_halts_the_loop_for_good() -> None:
    client = ScriptedWSClient(connect_outcomes=[Exception("401 unauthorized")])
    mgr = ReconnectManager(client, ReconnectConfig(initial_delay_s=0.01))
    await mgr.run()

    assert (client.connect_count, mgr.is_running) == (1, False)


@pytest.mark.asyncio
async def test_hourly_restart_budget_enforced() -> None:
    config = ReconnectConfig(initial_delay_s=0.001, max_delay_s=0.001, max_restarts_per_hour=3)
    client = ScriptedWSClient(connect_outcomes=[ConnectionError("fail")] * 10)
    mgr = ReconnectManager(client, config)
    await mgr.run()

    assert client.connect_count <= 4  # budget of 3 plus at most one in-flight retry


@pytest.mark.asyncio
async def test_stop_interrupts_a_long_backoff() -> None:
    client = ScriptedWSClient(connect_outcomes=[ConnectionError("fail")] * 10)
    mgr = ReconnectManager(client, ReconnectConfig(initial_delay_s=10.0, max_delay_s=60.0))

    async def halt_shortly() -> None:
        await asyncio.sleep(0.05)
        await mgr.stop()

    halter = asyncio.create_task(halt_shortly())
    await mgr.run()
    await halter

    assert mgr.is_running is False

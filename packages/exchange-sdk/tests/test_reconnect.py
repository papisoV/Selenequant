"""ReconnectManager: backoff growth, resubscription, auth abort, restart caps."""

from __future__ import annotations

import asyncio

import pytest

from selene_space_exchange.base.reconnect import ReconnectManager
from selene_space_exchange.base.ws_client import BaseWSClient
from selene_space_exchange.types import ReconnectConfig, WSClientConfig


class ScriptedWSClient(BaseWSClient):
    """WS client whose connect() replays a scripted list of outcomes."""

    def __init__(
        self, *, connect_outcomes: list[Exception | None] | None = None
    ) -> None:
        super().__init__(
            WSClientConfig(url="wss://scripted.invalid/feed", exchange="test")
        )
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


class RateLimitedWSClient(ScriptedWSClient):
    """Venue refuses the first N SUBSCRIBE frames with ``1008 Too many requests``.

    Binance answers a burst of subscribe frames with close code 1008 (policy
    violation), and it keeps refusing until the burst decays — so the frame the
    supervisor sends *next* is rejected too. That is the double:
    ``refuse_subscribes`` counts down and each refused frame raises, exactly as
    the live socket did. Once the budget is spent, frames are accepted and
    recorded.
    """

    def __init__(self, *, refuse_subscribes: int = 1, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.refusals_left = refuse_subscribes

    async def subscribe(self, streams: list[str]) -> None:
        if self.refusals_left > 0:
            self.refusals_left -= 1
            raise ConnectionError("received 1008 (policy violation) Too many requests")
        await super().subscribe(streams)


class DeadSocketAwareClient(BaseWSClient):
    """Client whose subscribe() raises unless a live socket is open.

    Models the real venue clients: ``subscribe()`` writes through to the
    socket, so after a drop it raises (``ConnectionClosedError`` on binance).
    ``ScriptedWSClient`` only mutates a set, which is exactly why the
    reconnect-ordering defect shipped unnoticed.
    """

    def __init__(self, *, drops: int = 1) -> None:
        super().__init__(
            WSClientConfig(url="wss://scripted.invalid/feed", exchange="test")
        )
        self._drops_left = drops
        #: None = never connected, True = live socket, False = dead socket.
        self._live: bool | None = None
        #: Pre-connect intent, flushed on connect like the real venue clients.
        self._buffered: list[str] = []
        self.connect_count = 0
        self.subscribe_calls: list[list[str]] = []
        #: Interleaved "connect"/"subscribe" order, for ordering assertions.
        self.timeline: list[str] = []

    async def connect(self) -> None:
        self.connect_count += 1
        self._live = True
        self.timeline.append("connect")
        await self._dispatch_connect()
        if self._buffered:
            streams, self._buffered = self._buffered, []
            await self._deliver(streams)
        if self._drops_left > 0:
            self._drops_left -= 1
            asyncio.create_task(self._drop_soon())

    async def _drop_soon(self) -> None:
        # Yield first so the listen loop is already awaiting the drop event.
        await asyncio.sleep(0.01)
        self._live = False
        await self._dispatch_disconnect()

    async def _deliver(self, streams: list[str]) -> None:
        self.timeline.append("subscribe")
        self.subscribe_calls.append(list(streams))

    async def subscribe(self, streams: list[str]) -> None:
        if self._live is False:
            raise ConnectionError("subscribe on a dead socket")
        self._subscriptions.update(streams)
        if self._live is None:
            self._buffered.extend(streams)
            return
        await self._deliver(streams)

    async def unsubscribe(self, streams: list[str]) -> None:
        self._subscriptions -= set(streams)

    async def close(self) -> None:
        self._live = False
        await self._dispatch_disconnect()


async def _run_with_halt(mgr: ReconnectManager, *, after: float = 0.2) -> None:
    """Run *mgr* to completion, stopping it after a settling delay."""

    async def halt() -> None:
        await asyncio.sleep(after)
        await mgr.stop()

    halter = asyncio.create_task(halt())
    await mgr.run()
    await halter


@pytest.mark.asyncio
async def test_clean_connect_leaves_counter_at_zero() -> None:
    client = ScriptedWSClient(connect_outcomes=[None])

    async def immediate_stop() -> None:
        await mgr.stop()

    mgr = ReconnectManager(
        client, ReconnectConfig(initial_delay_s=0.01, max_delay_s=0.05)
    )
    mgr._listen = immediate_stop
    await mgr.run()

    assert (mgr.attempt, client.connect_count) == (0, 1)


@pytest.mark.asyncio
async def test_delay_grows_with_each_failure() -> None:
    config = ReconnectConfig(
        initial_delay_s=1.0, max_delay_s=30.0, factor=2.0, jitter=0.0
    )
    mgr = ReconnectManager(ScriptedWSClient(), config)

    ladder = []
    for attempt in (1, 2, 3):
        mgr._attempt = attempt
        ladder.append(mgr._compute_delay())

    assert ladder[0] < ladder[1] < ladder[2]


@pytest.mark.asyncio
async def test_jitter_stays_inside_twenty_percent_band() -> None:
    config = ReconnectConfig(
        initial_delay_s=10.0, max_delay_s=30.0, factor=2.0, jitter=0.2
    )
    mgr = ReconnectManager(ScriptedWSClient(), config)
    mgr._attempt = 1

    samples = [mgr._compute_delay() for _ in range(200)]
    # Base for attempt 1 is initial_delay_s; jitter widens to [8.0, 12.0].
    assert all(
        7.9 <= d <= 12.1 for d in samples
    ), f"range: {min(samples)}..{max(samples)}"


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

    mgr = ReconnectManager(
        client, ReconnectConfig(initial_delay_s=0.01, max_delay_s=0.05), listen=listen
    )
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
    config = ReconnectConfig(
        initial_delay_s=0.001, max_delay_s=0.001, max_restarts_per_hour=3
    )
    client = ScriptedWSClient(connect_outcomes=[ConnectionError("fail")] * 10)
    mgr = ReconnectManager(client, config)
    await mgr.run()

    assert client.connect_count <= 4  # budget of 3 plus at most one in-flight retry


@pytest.mark.asyncio
async def test_stop_interrupts_a_long_backoff() -> None:
    client = ScriptedWSClient(connect_outcomes=[ConnectionError("fail")] * 10)
    mgr = ReconnectManager(
        client, ReconnectConfig(initial_delay_s=10.0, max_delay_s=60.0)
    )

    async def halt_shortly() -> None:
        await asyncio.sleep(0.05)
        await mgr.stop()

    halter = asyncio.create_task(halt_shortly())
    await mgr.run()
    await halter

    assert mgr.is_running is False


# --- venue-side drop that also refuses the resubscribe ------------------------
#
# 2026-10 Binance incident: the venue closed the market stream with
# ``1008 (policy violation) Too many requests`` and *then* rejected the
# SUBSCRIBE frame the supervisor sent next. The rejection escaped ``run()``,
# so the supervisor died on its first reconnect and the feed stayed down for
# good — every later REST read of that symbol answered 404. These two tests pin
# the supervisor contract: a failed resubscribe is a *reason to redial*, never a
# reason to stop supervising, and the streams must land on the new socket.


async def _drop_then_stop(client: ScriptedWSClient, mgr: ReconnectManager) -> None:
    """Drop the link once, then let a few redial cycles run before stopping."""
    await asyncio.sleep(0.02)
    await client._dispatch_disconnect(code=1008, reason="policy violation")
    await asyncio.sleep(0.08)
    await mgr.stop()


@pytest.mark.asyncio
async def test_supervisor_survives_a_refused_resubscribe() -> None:
    client = RateLimitedWSClient(refuse_subscribes=1)
    client._subscriptions.add("btcusdt@ticker")  # ledger seeded from a prior session

    mgr = ReconnectManager(
        client, ReconnectConfig(initial_delay_s=0.01, max_delay_s=0.02)
    )
    driver = asyncio.create_task(_drop_then_stop(client, mgr))
    try:
        await mgr.run()
    except Exception as exc:  # noqa: BLE001 — the defect IS this escape
        raise AssertionError(
            f"subscription failure escaped run() and killed the supervisor: {exc!r}"
        ) from exc
    await driver

    # The venue refused exactly one frame; the supervisor must have redialled
    # and pushed the streams again rather than dying on the refusal.
    assert client.refusals_left == 0, "scenario not exercised: venue never refused"
    assert (
        client.connect_count >= 2
    ), f"supervisor never redialled (connect_count={client.connect_count})"


@pytest.mark.asyncio
async def test_subscriptions_reach_the_socket_after_redial() -> None:
    client = RateLimitedWSClient(refuse_subscribes=1)
    client._subscriptions.add("btcusdt@ticker")

    mgr = ReconnectManager(
        client, ReconnectConfig(initial_delay_s=0.01, max_delay_s=0.02)
    )
    driver = asyncio.create_task(_drop_then_stop(client, mgr))
    await mgr.run()
    await driver

    delivered = [stream for call in client.subscribe_calls for stream in call]
    assert "btcusdt@ticker" in delivered, (
        "streams never reached the socket after redial "
        f"(refusals_left={client.refusals_left}, connects={client.connect_count})"
    )


class TestResubscribeLandsOnNewSocket:
    """Regression: resubscription must target the *reconnected* socket.

    Previously ``_resubscribe()`` sat at the tail of the loop body, so it ran
    *before* the next ``connect()`` — while the client's socket attribute
    still pointed at the dead connection. Frames went nowhere, and the raised
    send error escaped ``run()``, killing the supervisor on the first drop.
    """

    @pytest.mark.asyncio
    async def test_resubscribe_is_ordered_after_reconnect(self) -> None:
        client = DeadSocketAwareClient(drops=1)
        await client.subscribe(["btcusdt@ticker"])

        mgr = ReconnectManager(
            client, ReconnectConfig(initial_delay_s=0.01, max_delay_s=0.05)
        )
        await _run_with_halt(mgr, after=0.3)

        assert client.timeline == [
            "connect",
            "subscribe",
            "connect",
            "subscribe",
        ], f"resubscribe must follow the reconnect, got {client.timeline}"

    @pytest.mark.asyncio
    async def test_supervisor_survives_repeated_drops(self) -> None:
        client = DeadSocketAwareClient(drops=3)
        # A real subscription is load-bearing: with none, the old
        # ``_resubscribe()`` no-oped and the supervisor survived by accident,
        # so the test passed against the very defect it targets.
        await client.subscribe(["btcusdt@ticker"])

        mgr = ReconnectManager(
            client, ReconnectConfig(initial_delay_s=0.01, max_delay_s=0.05)
        )
        await _run_with_halt(mgr, after=0.5)

        assert (
            client.connect_count >= 4
        ), f"supervisor stopped redialling after {client.connect_count} connects"

    @pytest.mark.asyncio
    async def test_cold_connect_does_not_resubscribe(self) -> None:
        client = DeadSocketAwareClient(drops=0)
        await client.subscribe(["btcusdt@ticker"])

        mgr = ReconnectManager(
            client, ReconnectConfig(initial_delay_s=0.01, max_delay_s=0.05)
        )
        await _run_with_halt(mgr, after=0.2)

        # Exactly one delivery: the client's own pre-connect buffer flush.
        # The supervisor must not add a second subscribe of its own — on a
        # cold dial nothing has been lost yet.
        assert client.timeline == [
            "connect",
            "subscribe",
        ], f"cold start flushed once and only once, got {client.timeline}"
        assert len(client.subscribe_calls) == 1

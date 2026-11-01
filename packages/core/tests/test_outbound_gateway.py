"""Tests for selene_space_core.resilience.outbound_gateway."""

import asyncio
import time

import httpx
import pytest

from selene_space_core.resilience.circuit_breaker import CircuitBreaker, CircuitOpenError
from selene_space_core.resilience.outbound_gateway import OutboundGateway


def _mock_gateway(handler) -> OutboundGateway:
    """Build a gateway backed by a MockTransport handler."""
    transport = httpx.MockTransport(handler)
    return OutboundGateway(transport=transport)


class TestGetPost:
    @pytest.mark.asyncio
    async def test_get_returns_response(self):
        gateway = _mock_gateway(lambda req: httpx.Response(200, json={"ok": True}))
        resp = await gateway.get("https://api.binance.com/api/v3/ping")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        await gateway.aclose()

    @pytest.mark.asyncio
    async def test_get_sends_params(self):
        seen = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["url"] = str(req.url)
            return httpx.Response(200, json={})

        gateway = _mock_gateway(handler)
        await gateway.get("https://api.binance.com/api/v3/ticker", params={"symbol": "BTCUSDT"})
        assert "symbol=BTCUSDT" in seen["url"]
        await gateway.aclose()

    @pytest.mark.asyncio
    async def test_post_sends_json(self):
        seen = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["method"] = req.method
            seen["body"] = req.read().decode()
            return httpx.Response(200, json={})

        gateway = _mock_gateway(handler)
        await gateway.post("https://api.example.com/v1/chat", json={"q": "hi"})
        assert seen["method"] == "POST"
        assert '"q": "hi"' in seen["body"] or '"q":"hi"' in seen["body"]
        await gateway.aclose()


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_opens_after_failures(self):
        """Continuous transport failures open the circuit, then calls short-circuit."""
        state = {"calls": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            state["calls"] += 1
            raise httpx.ConnectError("connection refused")

        gateway = _mock_gateway(handler)
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=100)

        with pytest.raises(httpx.ConnectError):
            await gateway.get("https://api.binance.com/x", circuit=breaker)
        with pytest.raises(httpx.ConnectError):
            await gateway.get("https://api.binance.com/x", circuit=breaker)

        # Circuit is open — next call raises before hitting the network.
        with pytest.raises(CircuitOpenError):
            await gateway.get("https://api.binance.com/x", circuit=breaker)
        assert state["calls"] == 2
        await gateway.aclose()

    @pytest.mark.asyncio
    async def test_circuit_recovers_half_open(self):
        """After cooldown the circuit half-opens; a success closes it."""
        state = {"fail": True}

        def handler(req: httpx.Request) -> httpx.Response:
            if state["fail"]:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"ok": True})

        gateway = _mock_gateway(handler)
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)

        with pytest.raises(httpx.ConnectError):
            await gateway.get("https://api.binance.com/x", circuit=breaker)
        with pytest.raises(CircuitOpenError):
            await gateway.get("https://api.binance.com/x", circuit=breaker)

        await asyncio.sleep(0.1)
        state["fail"] = False
        resp = await gateway.get("https://api.binance.com/x", circuit=breaker)
        assert resp.json() == {"ok": True}
        assert breaker.state.value == "closed"
        await gateway.aclose()

    @pytest.mark.asyncio
    async def test_non_2xx_returns_response_not_raise(self):
        """Non-2xx does NOT raise by default — callers inspect status (httpx behavior)."""
        gateway = _mock_gateway(lambda req: httpx.Response(404))
        resp = await gateway.get("https://api.binance.com/x")
        assert resp.status_code == 404
        await gateway.aclose()

    @pytest.mark.asyncio
    async def test_default_circuit_opens_per_host(self):
        """default_circuit=True wires a per-host breaker without per-call opts."""
        state = {"calls": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            state["calls"] += 1
            if "binance" in req.url.host:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        gateway = OutboundGateway(
            transport=transport,
            default_circuit=True,
        )
        # Pre-seed a low-threshold breaker for api.binance.com so the test
        # doesn't need 5 consecutive failures (the gateway default).
        gateway._circuits["api.binance.com"] = CircuitBreaker(
            failure_threshold=2,
            recovery_timeout=100,
        )

        with pytest.raises(httpx.ConnectError):
            await gateway.get("https://api.binance.com/x")
        with pytest.raises(httpx.ConnectError):
            await gateway.get("https://api.binance.com/x")

        # Circuit for api.binance.com is now open — short-circuits.
        with pytest.raises(CircuitOpenError):
            await gateway.get("https://api.binance.com/x")
        assert state["calls"] == 2

        # A different host still works (per-host isolation).
        resp = await gateway.get("https://api.okx.com/ping")
        assert resp.status_code == 200
        await gateway.aclose()


class TestThreadIsolation:
    @pytest.mark.asyncio
    async def test_slow_request_does_not_block_loop(self):
        """A slow external request must not block the event loop (thread isolation)."""
        started = time.monotonic()

        def handler(req: httpx.Request) -> httpx.Response:
            time.sleep(0.5)  # Simulate a stalled external endpoint.
            return httpx.Response(200, json={"slow": True})

        gateway = _mock_gateway(handler)

        # Fire the slow request without awaiting, then measure loop responsiveness.
        slow_task = asyncio.create_task(gateway.get("https://api.binance.com/slow"))

        # Give the thread a chance to start, then check the loop stays responsive.
        await asyncio.sleep(0.1)
        tick_start = time.monotonic()
        await asyncio.sleep(0.1)
        tick_elapsed = time.monotonic() - tick_start
        # If the request ran on the event loop, the tick would take ~0.5s.
        assert tick_elapsed < 0.25, f"event loop blocked for {tick_elapsed:.2f}s"

        resp = await slow_task
        assert resp.json() == {"slow": True}
        total = time.monotonic() - started
        assert total >= 0.5
        await gateway.aclose()


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_aclose_closes_client(self):
        gateway = _mock_gateway(lambda req: httpx.Response(200))
        await gateway.get("https://api.binance.com/x")
        await gateway.aclose()
        assert gateway._closed is True

    @pytest.mark.asyncio
    async def test_use_after_close_raises(self):
        gateway = _mock_gateway(lambda req: httpx.Response(200))
        await gateway.aclose()
        with pytest.raises(RuntimeError, match="closed"):
            await gateway.get("https://api.binance.com/x")


class TestStream:
    @pytest.mark.asyncio
    async def test_stream_yields_lines(self):
        sse = "data: hello\ndata: world\n\n"

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

        gateway = _mock_gateway(handler)
        lines = [line async for line in gateway.stream("POST", "https://llm.example.com/v1/chat")]
        assert "data: hello" in lines
        assert "data: world" in lines
        await gateway.aclose()

    @pytest.mark.asyncio
    async def test_stream_slow_does_not_block_loop(self):
        """A slow SSE stream must not block the event loop (thread isolation)."""

        class SlowStream(httpx.SyncByteStream):
            def __iter__(self):
                time.sleep(0.3)
                yield b"data: first\n\n"
                time.sleep(0.3)
                yield b"data: second\n\n"
                time.sleep(0.3)

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=SlowStream())

        gateway = _mock_gateway(handler)

        async def drain():
            return [line async for line in gateway.stream("POST", "https://llm.example.com/v1/chat")]

        started = time.monotonic()
        task = asyncio.create_task(drain())

        await asyncio.sleep(0.1)
        tick_start = time.monotonic()
        await asyncio.sleep(0.1)
        tick_elapsed = time.monotonic() - tick_start
        assert tick_elapsed < 0.25, f"event loop blocked for {tick_elapsed:.2f}s"

        lines = await task
        assert "data: first" in lines
        assert "data: second" in lines
        assert time.monotonic() - started >= 0.5
        await gateway.aclose()

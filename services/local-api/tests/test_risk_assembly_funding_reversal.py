"""T1: build_funding_reversal_monitor — fetch_rate closure + kill/flatten integration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.core.risk_assembly import build_funding_reversal_monitor


def _slot(symbol: str = "BTCUSDT", net_position: float = 0.01) -> dict:
    return {
        "deployment_id": f"d-{symbol}",
        "account_id": "pa-1",
        "symbol": symbol,
        "strategy_id": "funding_arb",
        "net_position": net_position,
    }


class _Runner:
    def __init__(self, slots: list[dict]) -> None:
        self._slots = slots

    def funding_arb_slots(self) -> list[dict]:
        return self._slots


class _Fetcher:
    def __init__(self, items: list | None = None, error: Exception | None = None) -> None:
        self._items = items or []
        self._error = error

    async def get_all(self):
        if self._error is not None:
            raise self._error
        return self._items


def _rate(symbol: str = "BTC/USDT", funding: float = 0.0003) -> SimpleNamespace:
    return SimpleNamespace(
        exchange="binance", symbol=symbol, funding_rate=funding, mark_price=100.0,
    )


class _KS:
    def __init__(self) -> None:
        self.activated: list[tuple] = []

    def activate_scope(self, scope, scope_id, reason) -> None:  # type: ignore[no-untyped-def]
        self.activated.append((scope, scope_id, reason))


class _Flatten:
    def __init__(self) -> None:
        self.calls = 0

    async def fire(self, strategy_id=None, *, reason="") -> None:  # type: ignore[no-untyped-def]
        self.calls += 1


async def _fetch_rate_of(monitor) -> SimpleNamespace | None:  # type: ignore[no-untyped-def]
    return await monitor._fetch_rate()


def test_fetch_rate_direct_symbol_match() -> None:
    """Slot BTCUSDT matches slash-form fetcher BTC/USDT."""

    async def _run() -> None:
        monitor = build_funding_reversal_monitor(
            strategy_runner=_Runner([_slot("BTCUSDT")]),
            funding_fetcher=_Fetcher([_rate("BTC/USDT", 0.0003)]),
            kill_switch=_KS(),
            flatten=_Flatten().fire,
        )
        rate = await _fetch_rate_of(monitor)
        assert rate is not None
        assert rate.funding_rate == 0.0003
        assert rate.symbol == "BTC/USDT"

    asyncio.run(_run())


def test_fetch_rate_takes_worst_across_pairs() -> None:
    """Lowest funding rate across all arb slots is returned (worst = reversal risk)."""

    async def _run() -> None:
        monitor = build_funding_reversal_monitor(
            strategy_runner=_Runner([_slot("BTCUSDT"), _slot("ETHUSDT")]),
            funding_fetcher=_Fetcher([
                _rate("BTC/USDT", 0.0002),
                _rate("ETH/USDT", 0.0001),  # worse — must be selected
            ]),
            kill_switch=_KS(),
            flatten=_Flatten().fire,
        )
        rate = await _fetch_rate_of(monitor)
        assert rate is not None
        assert rate.funding_rate == 0.0001
        assert rate.symbol == "ETH/USDT"

    asyncio.run(_run())


def test_fetch_rate_none_when_no_arb_slots() -> None:
    async def _run() -> None:
        monitor = build_funding_reversal_monitor(
            strategy_runner=_Runner([]),
            funding_fetcher=_Fetcher([_rate("BTC/USDT", 0.0003)]),
            kill_switch=_KS(),
            flatten=_Flatten().fire,
        )
        assert await _fetch_rate_of(monitor) is None

    asyncio.run(_run())


def test_fetch_rate_none_when_no_matching_rate() -> None:
    """Fetcher has no item for the slot symbol → None (skip poll)."""

    async def _run() -> None:
        monitor = build_funding_reversal_monitor(
            strategy_runner=_Runner([_slot("BTCUSDT")]),
            funding_fetcher=_Fetcher([_rate("SOL/USDT", 0.0003)]),
            kill_switch=_KS(),
            flatten=_Flatten().fire,
        )
        assert await _fetch_rate_of(monitor) is None

    asyncio.run(_run())


def test_fetch_rate_none_when_fetcher_missing() -> None:
    async def _run() -> None:
        monitor = build_funding_reversal_monitor(
            strategy_runner=_Runner([_slot("BTCUSDT")]),
            funding_fetcher=None,  # not wired in paper/shadow config
            kill_switch=_KS(),
            flatten=_Flatten().fire,
        )
        assert await _fetch_rate_of(monitor) is None

    asyncio.run(_run())


def test_fetch_rate_none_when_fetcher_errors() -> None:
    """Upstream fetch failure → None, monitor poll loop survives."""

    async def _run() -> None:
        monitor = build_funding_reversal_monitor(
            strategy_runner=_Runner([_slot("BTCUSDT")]),
            funding_fetcher=_Fetcher(error=RuntimeError("upstream down")),
            kill_switch=_KS(),
            flatten=_Flatten().fire,
        )
        assert await _fetch_rate_of(monitor) is None

    asyncio.run(_run())


def test_reversal_fires_kill_once_and_flatten_once() -> None:
    """End-to-end via factory: reversal → STRATEGY kill once + flatten once."""

    async def _run() -> None:
        ks = _KS()
        flatten = _Flatten()
        monitor = build_funding_reversal_monitor(
            strategy_runner=_Runner([_slot("BTCUSDT")]),
            funding_fetcher=_Fetcher([_rate("BTC/USDT", -0.0002)]),
            kill_switch=ks,
            flatten=flatten.fire,
            poll_interval_s=0.01,
        )
        monitor.start()
        try:
            await asyncio.sleep(0.05)
        finally:
            await monitor.stop()

        assert monitor.triggered
        assert len(ks.activated) == 1
        scope, scope_id, reason = ks.activated[0]
        assert getattr(scope, "name", str(scope)) == "STRATEGY"
        assert scope_id == "funding_arb"
        assert "BTC/USDT" in reason
        assert flatten.calls == 1

    asyncio.run(_run())


def test_stop_clears_task() -> None:
    """After stop(), the poll loop task is cleared (shutdown safety)."""

    async def _run() -> None:
        monitor = build_funding_reversal_monitor(
            strategy_runner=_Runner([_slot("BTCUSDT")]),
            funding_fetcher=_Fetcher([_rate("BTC/USDT", 0.0003)]),
            kill_switch=_KS(),
            flatten=_Flatten().fire,
            poll_interval_s=60.0,
        )
        monitor.start()
        await monitor.stop()
        assert monitor._task is None

    asyncio.run(_run())

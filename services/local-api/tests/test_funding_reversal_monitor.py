"""Tests for the funding-reversal watchdog (#3).

Covers: reversal fires kill switch + flatten, sub-threshold no-op, idempotent
triggering, reset re-arm, optional-dependency degrade (no kill switch / no
flatten / failing action still unwinds), and the background poll loop.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.core.funding_reversal_monitor import FundingReversalMonitor


def _rate(funding: float, symbol: str = "BTC/USDT") -> SimpleNamespace:
    return SimpleNamespace(funding_rate=funding, symbol=symbol)


class _KS:
    """Records activate_scope calls like the real KillSwitch."""

    def __init__(self) -> None:
        self.activated: list[tuple] = []

    def activate_scope(self, scope, scope_id, reason: str) -> None:  # type: ignore[no-untyped-def]
        self.activated.append((scope, scope_id, reason))


class _Flatten:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls = 0
        self._error = error

    async def __call__(self) -> None:
        self.calls += 1
        if self._error is not None:
            raise self._error


class TestDetection:
    @pytest.mark.asyncio
    async def test_sub_threshold_rate_fires_kill_switch_and_flatten(self) -> None:
        ks, flatten = _KS(), _Flatten()
        monitor = FundingReversalMonitor(
            lambda: None, exit_threshold=0.0001,
            strategy_id="funding_arb", kill_switch=ks, flatten=flatten,
        )

        fired = await monitor.check(_rate(-0.0002))

        assert fired
        assert monitor.triggered
        assert len(ks.activated) == 1
        scope, scope_id, reason = ks.activated[0]
        # STRATEGY-scoped to the arb strategy, with a descriptive reason.
        assert getattr(scope, "name", str(scope)) == "STRATEGY"
        assert scope_id == "funding_arb"
        assert "BTC/USDT" in reason
        assert flatten.calls == 1

    @pytest.mark.asyncio
    async def test_positive_funding_above_threshold_does_not_fire(self) -> None:
        ks, flatten = _KS(), _Flatten()
        monitor = FundingReversalMonitor(
            lambda: None, exit_threshold=0.0001,
            strategy_id="funding_arb", kill_switch=ks, flatten=flatten,
        )

        # Positive funding = shorts get paid. No reversal.
        assert not await monitor.check(_rate(0.0003))
        # Exactly at threshold: not a reversal.
        assert not await monitor.check(_rate(0.0001))
        assert not ks.activated
        assert flatten.calls == 0
        assert not monitor.triggered

    @pytest.mark.asyncio
    async def test_is_idempotent_until_reset(self) -> None:
        ks, flatten = _KS(), _Flatten()
        monitor = FundingReversalMonitor(
            lambda: None, exit_threshold=0.0001,
            strategy_id="funding_arb", kill_switch=ks, flatten=flatten,
        )

        assert await monitor.check(_rate(-0.0002))
        # Even deeper reversal must not double-fire.
        assert not await monitor.check(_rate(-0.0005))
        # Rate recovered above threshold: no fire either.
        assert not await monitor.check(_rate(0.0003))
        assert len(ks.activated) == 1
        assert flatten.calls == 1

    @pytest.mark.asyncio
    async def test_reset_re_arms(self) -> None:
        ks, flatten = _KS(), _Flatten()
        monitor = FundingReversalMonitor(
            lambda: None, exit_threshold=0.0001,
            strategy_id="funding_arb", kill_switch=ks, flatten=flatten,
        )

        assert await monitor.check(_rate(-0.0002))
        monitor.reset()
        assert not monitor.triggered
        assert await monitor.check(_rate(-0.0001))
        assert len(ks.activated) == 2
        assert flatten.calls == 2

    @pytest.mark.asyncio
    async def test_records_last_funding_rate(self) -> None:
        monitor = FundingReversalMonitor(lambda: None, exit_threshold=0.0001)
        assert monitor.last_funding_rate is None
        await monitor.check(_rate(0.0005))
        assert monitor.last_funding_rate == 0.0005


class TestOptionalDependencies:
    @pytest.mark.asyncio
    async def test_fires_with_no_kill_switch_and_no_flatten(self) -> None:
        monitor = FundingReversalMonitor(lambda: None, exit_threshold=0.0001)
        assert await monitor.check(_rate(-0.001))
        assert monitor.triggered

    @pytest.mark.asyncio
    async def test_fires_without_strategy_id_skips_kill_switch(self) -> None:
        ks, flatten = _KS(), _Flatten()
        monitor = FundingReversalMonitor(
            lambda: None, exit_threshold=0.0001,
            kill_switch=ks, flatten=flatten,
        )
        assert await monitor.check(_rate(-0.001))
        assert not ks.activated
        assert flatten.calls == 1

    @pytest.mark.asyncio
    async def test_kill_switch_failure_still_flattens(self) -> None:
        class _BoomKS:
            def activate_scope(self, scope, scope_id, reason):  # type: ignore[no-untyped-def]
                raise RuntimeError("kill switch backend down")

        flatten = _Flatten()
        monitor = FundingReversalMonitor(
            lambda: None, exit_threshold=0.0001,
            strategy_id="funding_arb", kill_switch=_BoomKS(), flatten=flatten,
        )
        assert await monitor.check(_rate(-0.001))
        assert flatten.calls == 1

    @pytest.mark.asyncio
    async def test_flatten_failure_is_logged_not_raised(self, caplog) -> None:  # type: ignore[no-untyped-def]
        ks = _KS()
        monitor = FundingReversalMonitor(
            lambda: None, exit_threshold=0.0001,
            strategy_id="funding_arb", kill_switch=ks,
            flatten=_Flatten(error=RuntimeError("exchange down")),
        )
        with caplog.at_level(logging.ERROR, logger="app.core.funding_reversal_monitor"):
            assert await monitor.check(_rate(-0.001))
        # Kill switch still fired — re-entry stays blocked even though unwind failed.
        assert len(ks.activated) == 1


class TestPollLoop:
    @pytest.mark.asyncio
    async def test_run_polls_and_fires_on_reversal_then_keeps_polling(self) -> None:
        ks, flatten = _KS(), _Flatten()

        async def fetch() -> SimpleNamespace:
            return _rate(-0.0002)

        monitor = FundingReversalMonitor(
            fetch, exit_threshold=0.0001,
            strategy_id="funding_arb", kill_switch=ks, flatten=flatten,
            poll_interval_s=0.01,
        )
        monitor.start()
        try:
            await asyncio.sleep(0.05)
        finally:
            await monitor.stop()

        assert monitor.triggered
        assert len(ks.activated) == 1
        assert flatten.calls == 1

    @pytest.mark.asyncio
    async def test_run_skips_none_snapshot(self) -> None:
        ks, flatten = _KS(), _Flatten()

        async def fetch() -> SimpleNamespace | None:
            return None

        monitor = FundingReversalMonitor(
            fetch, exit_threshold=0.0001,
            strategy_id="funding_arb", kill_switch=ks, flatten=flatten,
            poll_interval_s=0.01,
        )
        monitor.start()
        try:
            await asyncio.sleep(0.03)
        finally:
            await monitor.stop()
        assert not monitor.triggered
        assert not ks.activated

    @pytest.mark.asyncio
    async def test_run_survives_fetch_errors(self) -> None:
        calls = {"n": 0}
        ks, flatten = _KS(), _Flatten()

        async def fetch():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("upstream fetch failed")
            return _rate(-0.0002)

        monitor = FundingReversalMonitor(
            fetch, exit_threshold=0.0001,
            strategy_id="funding_arb", kill_switch=ks, flatten=flatten,
            poll_interval_s=0.01,
        )
        monitor.start()
        try:
            await asyncio.sleep(0.05)
        finally:
            await monitor.stop()
        assert monitor.triggered
        assert len(ks.activated) == 1

    @pytest.mark.asyncio
    async def test_start_is_idempotent_and_stop_cancels(self) -> None:
        monitor = FundingReversalMonitor(
            lambda: None, exit_threshold=0.0001, poll_interval_s=0.01,
        )
        monitor.start()
        monitor.start()  # second start must not spawn a second loop
        await asyncio.sleep(0.02)
        await monitor.stop()
        await monitor.stop()  # double stop is safe

"""StallWatchdog arming, touching, disarming, and single-fire semantics."""

from __future__ import annotations

import asyncio

import pytest

from selene_space_exchange.base.stall_watchdog import StallTimeoutMeta, StallWatchdog


def _watchdog(timeout_s: float, fired: list[StallTimeoutMeta], *, handler=None) -> StallWatchdog:
    """Build a watchdog with a fast poll so tests stay under a second."""
    return StallWatchdog(
        timeout_s=timeout_s,
        check_interval_s=timeout_s / 3,
        on_timeout=handler if handler is not None else (lambda meta: fired.append(meta)),
    )


async def _settle(seconds: float) -> None:
    await asyncio.sleep(seconds)


class TestFiring:
    @pytest.mark.asyncio
    async def test_idle_beyond_threshold_trips(self) -> None:
        fired: list[StallTimeoutMeta] = []
        wd = _watchdog(0.1, fired)
        await wd.start()
        wd.arm()
        await _settle(0.25)
        wd.stop()

        assert len(fired) == 1
        assert (fired[0].idle_s >= 0.1, fired[0].timeout_s) == (True, 0.1)

    @pytest.mark.asyncio
    async def test_one_shot_per_arm_cycle(self) -> None:
        """The watchdog auto-disarms after firing; no repeat until re-armed."""
        fired: list[StallTimeoutMeta] = []
        wd = _watchdog(0.05, fired)
        await wd.start()
        wd.arm()
        await _settle(0.25)
        wd.stop()
        assert len(fired) == 1

    @pytest.mark.asyncio
    async def test_async_handler_supported(self) -> None:
        async def async_handler(meta: StallTimeoutMeta) -> None:
            fired.append(meta)

        fired: list[StallTimeoutMeta] = []
        wd = _watchdog(0.05, fired, handler=async_handler)
        await wd.start()
        wd.arm()
        await _settle(0.15)
        wd.stop()
        assert len(fired) == 1


class TestSuppression:
    @pytest.mark.asyncio
    async def test_touch_keeps_postponing_the_trip(self) -> None:
        fired: list[StallTimeoutMeta] = []
        wd = _watchdog(0.1, fired)
        await wd.start()
        wd.arm()

        for _ in range(5):
            await _settle(0.04)
            wd.touch()

        wd.stop()
        assert len(fired) == 0

    @pytest.mark.asyncio
    async def test_disarm_silences_an_armed_cycle(self) -> None:
        fired: list[StallTimeoutMeta] = []
        wd = _watchdog(0.08, fired)
        await wd.start()
        wd.arm()
        wd.disarm()
        await _settle(0.15)
        wd.stop()
        assert len(fired) == 0

    @pytest.mark.asyncio
    async def test_stop_halts_all_future_checks(self) -> None:
        fired: list[StallTimeoutMeta] = []
        wd = _watchdog(0.05, fired)
        await wd.start()
        wd.arm()
        wd.stop()
        await _settle(0.1)

        assert (len(fired), wd.is_stopped, wd.is_armed) == (0, True, False)


class TestRearmingAndConfig:
    @pytest.mark.asyncio
    async def test_second_arm_fires_again(self) -> None:
        fired: list[StallTimeoutMeta] = []
        wd = _watchdog(0.05, fired)
        await wd.start()

        for expected in (1, 2):
            wd.arm()
            await _settle(0.12)
            assert len(fired) == expected

        wd.stop()

    @pytest.mark.asyncio
    async def test_armed_flag_tracks_lifecycle(self) -> None:
        wd = StallWatchdog(timeout_s=1.0)
        assert wd.is_armed is False

        wd.arm()
        assert wd.is_armed is True
        wd.disarm()
        assert wd.is_armed is False

        wd.arm()
        wd.stop()
        assert wd.is_armed is False

    @pytest.mark.parametrize("bad_timeout", [0, -1], ids=["zero", "negative"])
    def test_nonpositive_timeout_rejected(self, bad_timeout: float) -> None:
        with pytest.raises(ValueError, match="positive"):
            StallWatchdog(timeout_s=bad_timeout)

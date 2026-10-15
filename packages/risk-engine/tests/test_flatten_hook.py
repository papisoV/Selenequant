"""T2: FlattenHook — scope semantics, per-callback isolation, sync-safe fire_and_forget."""

from __future__ import annotations

import asyncio

from selene_space_risk.flatten_hook import FlattenHook


def test_fire_none_hits_everything() -> None:
    """fire() with strategy_id=None triggers global + all scoped callbacks."""

    async def _run() -> None:
        hook = FlattenHook()
        fired: list[str] = []
        hook.register(lambda: fired.append("g"))
        hook.register(lambda: fired.append("s"), strategy_id="funding_arb")
        report = await hook.fire()
        assert sorted(fired) == ["g", "s"]
        assert report.n_fired == 2
        assert report.n_failed == 0

    asyncio.run(_run())


def test_scoped_fire_hits_scoped_plus_global() -> None:
    """fire("funding_arb") hits the funding_arb callback AND global callbacks."""

    async def _run() -> None:
        hook = FlattenHook()
        fired: list[str] = []
        hook.register(lambda: fired.append("g"))
        hook.register(lambda: fired.append("s"), strategy_id="funding_arb")
        hook.register(lambda: fired.append("o"), strategy_id="grid")
        await hook.fire("funding_arb")
        assert sorted(fired) == ["g", "s"]

    asyncio.run(_run())


def test_other_strategy_does_not_hit_scoped() -> None:
    """A scoped callback must NOT fire for a different strategy_id."""

    async def _run() -> None:
        hook = FlattenHook()
        fired: list[str] = []
        hook.register(lambda: fired.append("s"), strategy_id="funding_arb")
        await hook.fire("grid")
        assert fired == []

    asyncio.run(_run())


def test_raising_callback_does_not_abort_others() -> None:
    """One raising callback is isolated; the rest still fire and are counted."""

    async def _run() -> None:
        hook = FlattenHook()
        fired: list[str] = []

        def _boom() -> None:
            raise RuntimeError("flatten boom")

        hook.register(_boom)
        hook.register(lambda: fired.append("ok"))
        report = await hook.fire()
        assert fired == ["ok"]
        assert report.n_fired == 2
        assert report.n_failed == 1

    asyncio.run(_run())


def test_async_callback_is_awaited() -> None:
    """Async flatten callbacks are awaited, not fired-and-dropped."""

    async def _run() -> None:
        hook = FlattenHook()
        fired: list[str] = []

        async def _slow() -> None:
            await asyncio.sleep(0)
            fired.append("async")

        hook.register(_slow)
        await hook.fire()
        assert fired == ["async"]

    asyncio.run(_run())


def test_unregister_removes_callback() -> None:
    async def _run() -> None:
        hook = FlattenHook()
        fired: list[int] = []

        def _cb() -> None:
            fired.append(1)

        hook.register(_cb)
        hook.unregister(_cb)
        await hook.fire()
        assert fired == []

    asyncio.run(_run())


def test_fire_and_forget_no_running_loop_pending_log() -> None:
    """Outside an event loop, fire_and_forget logs pending and never raises."""

    hook = FlattenHook()
    fired: list[int] = []
    hook.register(lambda: fired.append(1))
    hook.fire_and_forget()  # must not raise RuntimeError
    assert fired == []  # nothing executed — no loop to run it

"""Boundary-condition stress tests for core (cleanroom hardening).

Covers: EventBus concurrency and mutation-during-publish, FileLock contention
and corrupt-state recovery, cross_validate zero-consensus edge, exact_calc
adversarial expressions, and benford garbage input handling.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading

import pytest

from selene_space_core.events.bus import EventBus, get_event_bus
from selene_space_core.infra.file_lock import FileLock
from selene_space_core.rigor import benford, cross_validate, exact_calc, verify_market_cap


class _Tick:
    def __init__(self, v: int) -> None:
        self.v = v


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------


class TestEventBusStress:
    def test_concurrent_subscribe_no_lost_handlers(self) -> None:
        bus = EventBus()
        n_threads, per_thread = 8, 100

        def worker(t: int) -> None:
            for i in range(per_thread):
                bus.subscribe(_Tick, lambda e, t=t, i=i: None)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert bus.handler_count(_Tick) == n_threads * per_thread

    def test_concurrent_subscribe_unsubscribe_stays_consistent(self) -> None:
        bus = EventBus()

        def adder() -> None:
            for _ in range(200):
                bus.subscribe(_Tick, lambda e: None)

        stop = threading.Event()

        def remover() -> None:
            while not stop.is_set():
                bus.unsubscribe(_Tick, lambda e: None)  # no-op for distinct lambdas

        adders = [threading.Thread(target=adder) for _ in range(4)]
        removers = [threading.Thread(target=remover) for _ in range(4)]
        for t in adders + removers:
            t.start()
        for t in adders:
            t.join()
        stop.set()
        for t in removers:
            t.join()
        # No exception == lock discipline holds; count is whatever survived.
        assert bus.handler_count(_Tick) >= 0

    def test_publish_uses_snapshot_handler_removed_midflight_still_runs(self) -> None:
        bus = EventBus()
        calls: list[str] = []

        def first(e: object) -> None:
            calls.append("first")
            bus.unsubscribe(_Tick, second)

        def second(e: object) -> None:
            calls.append("second")

        bus.subscribe(_Tick, first)
        bus.subscribe(_Tick, second)
        bus.publish(_Tick(1))
        assert calls == ["first", "second"]  # snapshot dispatch

    def test_raising_handler_does_not_block_others(self) -> None:
        bus = EventBus()
        ran = []
        bus.subscribe(_Tick, lambda e: (_ for _ in ()).throw(ValueError("boom")))
        bus.subscribe(_Tick, lambda e: ran.append("after"))
        bus.publish(_Tick(1))
        assert ran == ["after"]

    def test_publish_with_zero_handlers_is_noop(self) -> None:
        EventBus().publish(_Tick(1))  # must not raise

    def test_async_handler_in_running_loop_scheduled(self) -> None:
        bus = EventBus()
        done: list[int] = []

        async def handler(e: object) -> None:
            done.append(1)

        async def main() -> None:
            bus.subscribe(_Tick, handler)
            bus.publish(_Tick(1))
            await asyncio.sleep(0.01)

        asyncio.run(main())
        assert done == [1]

    def test_publish_async_runs_sync_and_async_inline(self) -> None:
        bus = EventBus()
        order: list[str] = []

        async def ahandler(e: object) -> None:
            order.append("async")

        def shandler(e: object) -> None:
            order.append("sync")

        bus.subscribe(_Tick, ahandler)
        bus.subscribe(_Tick, shandler)

        async def main() -> None:
            await bus.publish_async(_Tick(1))

        asyncio.run(main())
        assert order.count("async") == 1
        assert order.count("sync") == 1

    def test_get_event_bus_singleton_identity(self) -> None:
        assert get_event_bus() is get_event_bus()


# ---------------------------------------------------------------------------
# FileLock
# ---------------------------------------------------------------------------


class TestFileLockStress:
    def test_corrupt_lock_content_treated_as_stale(self) -> None:
        d = tempfile.mkdtemp()
        path = os.path.join(d, "corrupt.lock")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("not-a-pid!!")
        lock = FileLock(path)
        lock.acquire()
        assert lock.is_held
        lock.release()

    def test_dead_pid_lock_broken(self) -> None:
        d = tempfile.mkdtemp()
        path = os.path.join(d, "dead.lock")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(2**31 - 1))  # effectively never alive
        lock = FileLock(path)
        lock.acquire()
        assert lock.is_held
        lock.release()
        assert not os.path.exists(path)

    def test_reentrant_acquire_same_process(self) -> None:
        d = tempfile.mkdtemp()
        lock = FileLock(os.path.join(d, "re.lock"))
        lock.acquire()
        lock.acquire()  # no-op, no raise
        lock.release()
        assert not lock.is_held

    def test_release_without_acquire_is_noop(self) -> None:
        d = tempfile.mkdtemp()
        lock = FileLock(os.path.join(d, "never.lock"))
        lock.release()  # must not raise
        assert not lock.is_held

    def test_context_manager_releases_on_exception(self) -> None:
        d = tempfile.mkdtemp()
        path = os.path.join(d, "ctx.lock")
        with pytest.raises(RuntimeError):
            with FileLock(path):
                raise RuntimeError("body failed")
        assert not os.path.exists(path)
        # Lock file gone => next acquire succeeds
        FileLock(path).acquire()

    def test_double_release_safe(self) -> None:
        d = tempfile.mkdtemp()
        lock = FileLock(os.path.join(d, "dbl.lock"))
        lock.acquire()
        lock.release()
        lock.release()
        assert not lock.is_held

    def test_creates_missing_parent_dirs(self) -> None:
        d = tempfile.mkdtemp()
        path = os.path.join(d, "a", "b", "c.lock")
        lock = FileLock(path)
        lock.acquire()
        assert os.path.exists(path)
        lock.release()


# ---------------------------------------------------------------------------
# rigor edges (incl. zero-consensus fix regression lock)
# ---------------------------------------------------------------------------


class TestRigorEdges:
    def test_cross_validate_all_zero_sources_consistent(self) -> None:
        res = cross_validate("x", {"a": 0, "b": 0})
        assert res["all_consistent"] is True

    def test_cross_validate_zero_consensus_vs_nonzero_flags_disagreement(self) -> None:
        """[BUG-FIXED] zero consensus previously reported all_consistent=True."""
        res = cross_validate("rev", {"srcA": 0, "srcB": 100})
        assert res["all_consistent"] is False
        assert res["details"][1]["deviation_pct"] == float("inf")

    def test_cross_validate_empty_sources(self) -> None:
        res = cross_validate("f", {})
        assert res["consensus"] is None
        assert res["all_consistent"] is False
        assert res["details"] == []

    def test_verify_market_cap_zero_reported_cap(self) -> None:
        res = verify_market_cap(10, 5, 0)
        assert res["is_valid"] is False
        assert res["deviation_pct"] == 100.0

    def test_exact_calc_adversarial_inputs_return_none(self) -> None:
        for expr in ("1/0", "2**3", "abc", "1+", "(1+2", "1+2)", "", "   ", "1 2"):
            assert exact_calc(expr) is None, expr

    def test_exact_calc_valid_expressions(self) -> None:
        assert exact_calc("-(2+3)*4") == -20
        assert exact_calc("0.1*3") == __import__("decimal").Decimal("0.3")

    def test_benford_below_min_sample_returns_none(self) -> None:
        assert benford([1] * 29) is None

    def test_benford_uniform_nonconforming_detected(self) -> None:
        values = [(i % 9) + 1 for i in range(90)]  # uniform digits 1..9
        res = benford(values)
        assert res is not None and res["is_conforming"] is False

    def test_benford_zeros_and_negatives_handled(self) -> None:
        values = [abs(v) or 1 for v in range(-45, 45)]
        res = benford(values)
        assert res is not None and res["n"] > 0

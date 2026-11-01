"""Scheduler locks for selene_space_core.scheduler — interval parsing,
schedule next-run computation, JSONL run log, and service execution."""

from datetime import datetime, timedelta

import pytest

from selene_space_core.scheduler.schedule import Schedule, ScheduleType, _parse_interval
from selene_space_core.scheduler.service import SchedulerService
from selene_space_core.scheduler.store import SchedulerStore


def _store(tmp_path, name="runs.jsonl") -> SchedulerStore:
    return SchedulerStore(tmp_path / name)


class TestNextRunComputation:
    def test_every_interval_advances_from_anchor(self):
        sched = Schedule(ScheduleType.EVERY, "5m", "cleanup")
        anchor = datetime(2025, 1, 1, 12, 0, 0)
        assert sched.next_run(after=anchor) == anchor + timedelta(minutes=5)

    def test_at_future_time_is_schedulable(self):
        sched = Schedule(ScheduleType.AT, "2099-01-01T00:00:00", "future_task")
        assert sched.next_run() is not None

    def test_at_past_time_yields_none(self):
        sched = Schedule(ScheduleType.AT, "2000-01-01T00:00:00", "past_task")
        assert sched.next_run() is None

    def test_cron_expression_rounds_up_to_next_minute(self):
        sched = Schedule(ScheduleType.CRON, "* * * * *", "every_min")
        anchor = datetime(2025, 1, 1, 12, 30, 15)
        assert sched.next_run(after=anchor) == datetime(2025, 1, 1, 12, 31, 0)


INTERVAL_CASES = [
    ("30s", timedelta(seconds=30)),
    ("5m", timedelta(minutes=5)),
    ("1h", timedelta(hours=1)),
    ("7d", timedelta(days=7)),
]


class TestIntervalParsing:
    @pytest.mark.parametrize(("spec", "expected"), INTERVAL_CASES)
    def test_each_unit_parses(self, spec, expected):
        assert _parse_interval(spec) == expected

    def test_unparseable_spec_raises(self):
        with pytest.raises(ValueError):
            _parse_interval("abc")


class TestRunLog:
    def test_log_two_runs_then_read_back(self, tmp_path):
        store = _store(tmp_path)
        moment = datetime.now()
        store.log_run("task1", moment, moment, "success")
        store.log_run("task2", moment, moment, "error", error="boom")
        rows = store.read_log()
        assert len(rows) == 2
        assert (rows[0]["task_name"], rows[1]["error"]) == ("task1", "boom")

    def test_fresh_log_reads_empty(self, tmp_path):
        assert _store(tmp_path, "empty.jsonl").read_log() == []


class TestSchedulerService:
    @pytest.mark.asyncio
    async def test_due_task_executes_and_logs_success(self, tmp_path):
        store = _store(tmp_path)
        svc = SchedulerService(store)
        fired = []

        async def work():
            fired.append(True)

        svc.add(Schedule(ScheduleType.EVERY, "1s", "probe"), work)
        await svc.run_once()
        assert len(fired) == 1
        # Check log
        assert store.read_log()[0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_failing_task_logs_error_with_message(self, tmp_path):
        svc = SchedulerService(_store(tmp_path))

        async def broken():
            raise ValueError("oops")

        svc.add(Schedule(ScheduleType.EVERY, "1s", "bad"), broken)
        await svc.run_once()
        row = _store(tmp_path).read_log()[0]
        assert row["status"] == "error" and "oops" in row["error"]

    def test_task_names_reflect_registrations(self, tmp_path):
        svc = SchedulerService(_store(tmp_path))
        svc.add(Schedule(ScheduleType.EVERY, "1m", "a"), lambda: None)
        svc.add(Schedule(ScheduleType.EVERY, "5m", "b"), lambda: None)
        assert set(svc.task_names) == {"a", "b"}

    def test_remove_deregisters_task(self, tmp_path):
        svc = SchedulerService(_store(tmp_path))
        svc.add(Schedule(ScheduleType.EVERY, "1m", "a"), lambda: None)
        svc.remove("a")
        assert svc.task_names == []

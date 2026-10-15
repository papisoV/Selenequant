"""Scheduler service: register task schedules, execute due tasks, log results."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from selene_space_core.scheduler.schedule import Schedule
from selene_space_core.scheduler.store import SchedulerStore


@dataclass
class _Task:
    schedule: Schedule
    fn: Callable


class SchedulerService:
    """Runs each registered task at most once per run_once() invocation and
    records every attempt (success or error) in the store. Task failures are
    captured, never raised."""

    def __init__(self, store: SchedulerStore) -> None:
        self._store = store
        self._tasks: dict[str, _Task] = {}

    def add(self, schedule: Schedule, fn: Callable) -> None:
        self._tasks[schedule.task_name] = _Task(schedule=schedule, fn=fn)

    def remove(self, task_name: str) -> None:
        self._tasks.pop(task_name, None)

    @property
    def task_names(self) -> list[str]:
        return list(self._tasks.keys())

    async def run_once(self) -> None:
        for name in list(self._tasks.keys()):
            await self._run_task(name)

    async def _run_task(self, name: str) -> None:
        task = self._tasks.get(name)
        if task is None:
            return
        started = datetime.now()
        try:
            outcome = task.fn()
            if inspect.isawaitable(outcome):
                await outcome
        except Exception as exc:
            self._store.log_run(name, started, datetime.now(), "error", error=str(exc))
            return
        self._store.log_run(name, started, datetime.now(), "success")

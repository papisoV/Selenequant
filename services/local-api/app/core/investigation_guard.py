"""进程内的"调查会正在跑"注册表 —— 人手点和自动续研共用的唯一一把锁。

为什么单独成模块而不是留在 ``api/v1/research_graph.py``:自动续研那个后台
循环不在路由层,却必须和人手点的 ``POST /questions/{id}/investigate`` 抢同一
个名额。两处各记一份的话,同一个问题会被两轮调查会各付一次三视角 LLM 账单,
而两边都以为自己防住了。

注册表是进程内的(on restart 即失),这是可接受的简化:调查会的进度落在
``research_question_events``(``investigate_perspective`` /
``investigate_finished``),重启后前端靠"事件缺席 + running=False"就能收敛到
诚实状态。锁的职责只是"别在这一进程的同一时刻重复付费",不是持久真相。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

#: qid → 后台 Task。谁先拿到谁跑,后到者拿到 409。
_INVESTIGATE_TASKS: dict[str, "asyncio.Task[None]"] = {}

#: qid → 任务开始时刻(ISO)。Task 对象拿不到起点,而界面要显示"跑了多久",
#: 才能区分"刚开始"和"卡住了"。任务结束随 done_callback 一并清掉。
_INVESTIGATE_STARTED_AT: dict[str, str] = {}


def is_running(question_id: str) -> bool:
    """该问题此刻是否有调查会在跑(惰性清理已结束的幽灵项)。"""
    task = _INVESTIGATE_TASKS.get(question_id)
    return task is not None and not task.done()


def register(question_id: str, task: "asyncio.Task[None]") -> None:
    """登记一个正在跑的调查会,并在结束时自动摘除。"""
    _INVESTIGATE_TASKS[question_id] = task
    _INVESTIGATE_STARTED_AT[question_id] = datetime.now(UTC).isoformat(
        timespec="seconds"
    )

    def _on_done(_t: "asyncio.Task[None]") -> None:
        _INVESTIGATE_TASKS.pop(question_id, None)
        _INVESTIGATE_STARTED_AT.pop(question_id, None)

    task.add_done_callback(_on_done)


def forget(question_id: str) -> None:
    """无条件摘掉登记(取消路径用:任务已 cancel,不等 done_callback 也行)。"""
    _INVESTIGATE_TASKS.pop(question_id, None)
    _INVESTIGATE_STARTED_AT.pop(question_id, None)


def running_investigations() -> dict[str, str]:
    """此刻仍在跑的调查会:qid → started_at(ISO)。给聚合端点读,别直接摸私有表。

    顺带做一次惰性清理:若某个 Task 已 done 但 done_callback 还没轮到
    (事件循环被长时间堵住),这里按"已结束"处理,不让幽灵 running 泄漏到 UI。
    """
    out: dict[str, str] = {}
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for qid, task in list(_INVESTIGATE_TASKS.items()):
        if task.done():
            continue
        out[qid] = _INVESTIGATE_STARTED_AT.get(qid, now)
    return out


__all__ = [
    "forget",
    "is_running",
    "register",
    "running_investigations",
]

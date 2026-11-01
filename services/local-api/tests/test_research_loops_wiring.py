"""Lifespan 接线测试:两个研究循环真的被 create_task,且关停有东西可 cancel。

这是 US-005 的验收:模块写好了、测试绿了,但没人调用它 —— 那正是
"investigation 黑洞"的同一类错误(有读写两面,没有消费者)。这里钉住
``_start_research_loops`` 的四个分支。
"""

from __future__ import annotations

import asyncio

import pytest

from app.main import _start_research_loops


class _Settings:
    def __init__(self, section: dict | None = None) -> None:
        self._section = section or {
            "enabled": True,
            "interval_minutes": 15,
            "max_rounds_per_mission": 3,
        }

    def get_mission_worker(self) -> dict:
        return dict(self._section)


class _ExplodingSettings:
    def get_mission_worker(self) -> dict:
        raise RuntimeError("settings unavailable")


@pytest.mark.asyncio
async def test_starts_both_loops_when_storage_present() -> None:
    state = {"db_manager": object(), "settings_service": _Settings()}
    _start_research_loops(state)
    try:
        worker = state["_mission_worker_task"]
        watcher = state["_revisit_watch_task"]
        assert isinstance(worker, asyncio.Task)
        assert isinstance(watcher, asyncio.Task)
        assert worker.get_name() == "mission-worker"
        assert watcher.get_name() == "revisit-watcher"
        # 第三颗循环:自动续研(默认关闭,但必须接线 —— 否则开关打开也不会有
        # 任何东西去跑,正是本文件 docstring 说的"有读写两面,没有消费者")。
        autorenew = state["_research_autorenew_task"]
        assert isinstance(autorenew, asyncio.Task)
        assert autorenew.get_name() == "research-autorenew"
    finally:
        for key in (
            "_mission_worker_task",
            "_revisit_watch_task",
            "_research_autorenew_task",
        ):
            task = state.get(key)
            if isinstance(task, asyncio.Task):
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task


@pytest.mark.asyncio
async def test_no_storage_leaves_sentinels_none() -> None:
    """没有存储时哨兵都是 None —— shutdown 的 state.get() 不会拿到未完成任务。"""
    state = {"db_manager": None, "settings_service": _Settings()}
    _start_research_loops(state)
    assert state["_mission_worker_task"] is None
    assert state["_revisit_watch_task"] is None
    assert state["_research_autorenew_task"] is None


@pytest.mark.asyncio
async def test_broken_settings_still_starts_loops_with_default_interval() -> None:
    """读设置炸了也不能拖垮启动:退回默认 15 分钟,循环照跑。"""
    state = {"db_manager": object(), "settings_service": _ExplodingSettings()}
    _start_research_loops(state)
    try:
        assert isinstance(state["_mission_worker_task"], asyncio.Task)
        assert isinstance(state["_revisit_watch_task"], asyncio.Task)
        assert isinstance(state["_research_autorenew_task"], asyncio.Task)
    finally:
        for key in (
            "_mission_worker_task",
            "_revisit_watch_task",
            "_research_autorenew_task",
        ):
            task = state.get(key)
            if isinstance(task, asyncio.Task):
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task


@pytest.mark.asyncio
async def test_disabled_setting_still_wires_loop_that_idles() -> None:
    """enabled=False 时仍接线 —— 循环自己空转,用户打开开关无需重启。"""
    state = {
        "db_manager": object(),
        "settings_service": _Settings(
            {"enabled": False, "interval_minutes": 15, "max_rounds_per_mission": 3}
        ),
    }
    _start_research_loops(state)
    try:
        assert isinstance(state["_mission_worker_task"], asyncio.Task)
        assert not state["_mission_worker_task"].done()
    finally:
        for key in (
            "_mission_worker_task",
            "_revisit_watch_task",
            "_research_autorenew_task",
        ):
            task = state.get(key)
            if isinstance(task, asyncio.Task):
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

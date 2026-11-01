"""常驻循环骨架 — 三个后台 worker(collector / tagger / detector)共用。

每个 worker 的循环体形状完全一样: 跑一轮 -> 成功记心跳 -> 失败记错误 ->
睡一会儿。差别只在"跑哪一轮"和"睡多久"。与其在三处各抄一遍 while/try/except
嵌套, 不如把骨架收在这里, 各模块只提供两个回调。

用法::

    await run_forever(
        tick=lambda: collect_round(store),
        on_success=lambda: touch(store, ok=True),
        on_failure=lambda: touch(store, ok=False),
        interval=lambda: NEWS_POLL_INTERVAL,
        label="collector",
    )

``tick`` 返回真值表示这一轮有产出(记成功心跳), 假值同样记成功 —— 空轮也是
正常的一轮。真正的异常才走 ``on_failure``。

取消语义: ``asyncio.CancelledError`` 一律向上传播, 不被 ``except Exception``
吞掉 —— 进程退出时取消必须能穿透循环。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

#: 一轮跑完之后, 取下次睡眠秒数。用回调是为了让 tagger 把退避叠加进去。
IntervalFn = Callable[[], float]
#: 跑一轮。返回值只作日志/心跳判定用(当前实现里真值/假值都算成功)。
TickFn = Callable[[], Awaitable[object]]
#: 记录本轮结果(心跳或错误计数)。自身异常会被吞掉并记日志 —— 记账失败
#: 不该拖垮主循环。
Hook = Callable[[], Awaitable[None]]


async def _safe_hook(hook: Optional[Hook], label: str) -> None:
    """跑一个记账回调; 回调自己炸了只记日志, 不向上抛。"""
    if hook is None:
        return
    try:
        await hook()
    except Exception:
        logger.exception("[%s] 记账回调失败", label)


async def run_forever(
    *,
    tick: TickFn,
    interval: IntervalFn,
    label: str,
    on_success: Optional[Hook] = None,
    on_failure: Optional[Hook] = None,
) -> None:
    """常驻循环: 跑一轮 -> 记成功 -> 睡; 抛异常则记失败 -> 睡。

    永不返回(除非被取消)。``on_success`` 在 ``tick`` 正常返回后调用,
    ``on_failure`` 在 ``tick`` 抛 ``Exception`` 后调用 —— 两者都不影响
    下一轮继续跑。
    """
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise  # 取消必须穿透, 不能被下面的 except Exception 吞掉
        except Exception:
            logger.exception("[%s] 单轮失败", label)
            await _safe_hook(on_failure, label)
        else:
            await _safe_hook(on_success, label)
        await asyncio.sleep(interval())
